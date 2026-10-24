"""Two-step sign-in with an authenticator app (Google Authenticator,
Microsoft Authenticator, 1Password, ...): time-based six-digit codes, RFC 6238.

The secret is stored encrypted with a key derived from SECRET_KEY, so a copy
of the database alone can't produce codes. Each code works once (the last
used time step is remembered) and codes from one step either side are
accepted to allow for clock drift.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

from config import Config

STEP = 30  # seconds per code
DIGITS = 6
ISSUER = "AVIR AI"


def new_secret() -> str:
    """A random base32 secret (160 bits), as authenticator apps expect."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _fernet() -> Fernet:
    key = hashlib.sha256(("totp:" + str(Config.SECRET_KEY)).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None  # SECRET_KEY changed: two-step sign-in has to be set up again


def _code_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**DIGITS)
    return str(number).zfill(DIGITS)


def check_code(secret: str, code: str, last_counter: int | None = None, now: float | None = None) -> int | None:
    """The time step the code belongs to when it is valid and not used
    before, else None. Store the returned step so the code can't be reused."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS or not secret:
        return None
    current = int((now if now is not None else time.time()) // STEP)
    for counter in (current - 1, current, current + 1):
        if last_counter is not None and counter <= last_counter:
            continue
        if hmac.compare_digest(_code_at(secret, counter), code):
            return counter
    return None


def provisioning_uri(secret: str, account: str) -> str:
    """otpauth:// link (and QR code content) for adding the account to an app."""
    label = quote(f"{ISSUER}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&digits={DIGITS}&period={STEP}"
