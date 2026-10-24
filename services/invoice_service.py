"""GST tax invoices and credit notes for payments (services/billing_service.py).

- Every payment gets an invoice when it is applied; every refund gets a
  credit note against that invoice.
- Numbers: "<PREFIX>/<YY-YY>/<NNNNN>" for invoices and "CN/<YY-YY>/<NNNNN>" for
  credit notes, consecutive within each financial year (April to March), at
  most 16 characters as GST requires.
- Tax: the price paid includes 18% GST. Same state as the seller (or no state
  on record) -> CGST 9% + SGST 9%; another state -> IGST 18%. USD payments are
  export of services: zero-rated, with the LUT note.
- The seller's and buyer's details are copied into the invoice when it is
  issued, so later edits don't change it.
"""

import logging
import re
from datetime import datetime, timezone

from config import Config

logger = logging.getLogger(__name__)

CREDIT_NOTE_PREFIX = "CN"

# GST state codes (first two digits of a GSTIN)
STATES = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
}
STATE_CODES = {name: code for code, name in STATES.items()}
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def valid_gstin(gstin: str) -> bool:
    return bool(GSTIN_RE.match(gstin or "")) and gstin[:2] in STATES


def financial_year(when: datetime) -> str:
    """ "26-27" for April 2026 to March 2027."""
    start = when.year if when.month >= 4 else when.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def split_tax(total_with_gst: int, buyer_state: str | None) -> dict:
    """Taxable value and GST parts of an amount that includes 18% GST."""
    rate = Config.GST_RATE
    taxable = int(round(total_with_gst / (1 + rate)))
    gst = total_with_gst - taxable
    intra = not buyer_state or buyer_state == Config.INVOICE_SELLER_STATE
    if intra:
        cgst = gst // 2
        return {"taxable": taxable, "cgst": cgst, "sgst": gst - cgst, "igst": 0, "intra_state": True}
    return {"taxable": taxable, "cgst": 0, "sgst": 0, "igst": gst, "intra_state": False}


def _seller() -> dict:
    return {
        "name": Config.INVOICE_SELLER_NAME,
        "address": Config.INVOICE_SELLER_ADDRESS,
        "gstin": Config.INVOICE_SELLER_GSTIN,
        "state": Config.INVOICE_SELLER_STATE,
        "state_code": STATE_CODES.get(Config.INVOICE_SELLER_STATE, ""),
    }


def _issue(kind: str, prefix: str, payment: dict, amount: int, buyer: dict, extra: dict) -> dict | None:
    from db import next_invoice_number, save_invoice

    now = datetime.now(timezone.utc)
    series = f"{prefix}/{financial_year(now)}"
    if payment["currency"] == "INR":
        tax = split_tax(amount, buyer.get("state"))
        notes = []
    else:
        tax = {"taxable": amount, "cgst": 0, "sgst": 0, "igst": 0, "intra_state": None}
        notes = [Config.INVOICE_EXPORT_NOTE]
    details = {
        "seller": _seller(),
        "buyer": buyer,
        "sac": Config.INVOICE_SAC,
        "line": {"description": payment["description"], "item": payment["item"]},
        "place_of_supply": buyer.get("state")
        or (Config.INVOICE_SELLER_STATE if payment["currency"] == "INR" else "Outside India"),
        "payment": {
            "provider": payment["provider"],
            "order_id": payment["order_id"],
            "payment_id": payment.get("payment_id"),
        },
        "notes": notes,
        **extra,
    }
    for _ in range(5):  # two invoices at once can race for a number: try the next one
        number, sequence = next_invoice_number(series)
        saved = save_invoice(
            number=number,
            kind=kind,
            series=series,
            sequence=sequence,
            order_id=payment["order_id"],
            original_number=extra.get("original_number"),
            user_id=payment["user_id"],
            issued_at=now,
            currency=payment["currency"],
            taxable=tax["taxable"],
            cgst=tax["cgst"],
            sgst=tax["sgst"],
            igst=tax["igst"],
            total=amount,
            details=details,
        )
        if saved:
            return saved
    logger.error(f"Could not number the {kind} for order {payment['order_id']}")
    return None


def issue_invoice(payment: dict) -> dict | None:
    """The tax invoice for an applied payment (once per order)."""
    from db import get_billing_profile, invoices_for_order

    existing = [i for i in invoices_for_order(payment["order_id"]) if i["kind"] == "invoice"]
    if existing:
        return existing[0]
    buyer = get_billing_profile(payment["user_id"])
    return _issue(
        "invoice", (Config.INVOICE_PREFIX or "INV")[:4], payment, payment["amount"], buyer, {}
    )  # numbers stay within 16 characters


def issue_credit_note(payment: dict, refunded: int) -> dict | None:
    """A credit note for (part of) a refund, against the order's invoice."""
    from db import invoices_for_order

    invoices = invoices_for_order(payment["order_id"])
    original = next((i for i in invoices if i["kind"] == "invoice"), None)
    if original is None:
        original = issue_invoice(payment)
    buyer = (original or {}).get("details", {}).get("buyer", {})
    return _issue(
        "credit_note",
        CREDIT_NOTE_PREFIX,
        payment,
        refunded,
        buyer,
        {"original_number": original["number"] if original else None, "reason": "Refund"},
    )
