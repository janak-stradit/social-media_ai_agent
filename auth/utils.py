from functools import wraps

from flask import jsonify, redirect, session, url_for

from db import get_user_by_id


def get_current_user_id() -> int | None:
    user_id = session.get("user_id")
    return int(user_id) if user_id is not None else None


def login_required_api(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not get_current_user_id():
            return jsonify({"error": "Authentication required", "success": False}), 401
        return view(*args, **kwargs)

    return wrapped


def admin_required_api(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = get_current_user_id()
        if not user_id:
            return jsonify({"error": "Authentication required", "success": False}), 401
        user = get_user_by_id(user_id)
        if not user or not getattr(user, "is_admin", False):
            return jsonify({"error": "Admin access required", "success": False}), 403
        return view(*args, **kwargs)

    return wrapped


def login_required_page(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = get_current_user_id()
        if not user_id:
            return redirect(url_for("landing_page"))

        # Post-signup onboarding gate: verify email -> pick account type
        # (+ website for self-serve tiers, or Contact Sales for enterprise) ->
        # be active (self-serve tiers default active; Enterprise stays
        # inactive until sales/an admin flips it - see set_user_active in
        # db.py; deactivated accounts of any tier land here too).
        # verify_pending_page is NOT decorated with this (registration no
        # longer creates a session, so there's nothing to check yet there).
        # The onboarding/pending pages themselves ARE decorated but excluded
        # from the checks they'd otherwise trigger on themselves, which would
        # loop.
        exempt_views = ("onboarding_page", "onboarding_contact_sales_page", "account_pending_page")
        if view.__name__ not in exempt_views:
            user = get_user_by_id(user_id)
            # Admins (StradIT's own staff accounts) never go through the
            # customer onboarding funnel - without this, any admin account
            # that predates this feature (email_verified/onboarding_completed
            # both default False) gets stuck at /verify-pending and can never
            # reach even admin-only pages like /admin.
            if user and not getattr(user, "is_admin", False):
                if not user.email_verified:
                    return redirect(url_for("verify_pending_page"))
                if user.email_verified and not user.onboarding_completed:
                    if user.account_type == "enterprise":
                        return redirect(url_for("onboarding_contact_sales_page"))
                    return redirect(url_for("onboarding_page"))
                if user.onboarding_completed and not getattr(user, "is_active", True):
                    return redirect(url_for("account_pending_page"))

        return view(*args, **kwargs)

    return wrapped


def admin_required_page(view):
    """Gates the standalone Admin Control Center page to admins only -
    page-level equivalent of admin_required_api. Must be applied together
    with (inside) @login_required_page."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = get_current_user_id()
        user = get_user_by_id(user_id) if user_id else None
        if not user or not getattr(user, "is_admin", False):
            return redirect(url_for("index"))
        return view(*args, **kwargs)

    return wrapped


def self_serve_required_page(view):
    """Gates the "My Brand Configuration" page (per-user UserBrandProfile,
    see db.py) to Individual/Small/Medium accounts - Enterprise accounts
    don't have a scraped brand profile (they go through Sales instead), and
    admins have no brand profile of their own to edit. Must be applied
    together with (inside) @login_required_page."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = get_current_user_id()
        user = get_user_by_id(user_id) if user_id else None
        if not user or user.account_type not in ("individual", "small", "medium"):
            return redirect(url_for("index"))
        return view(*args, **kwargs)

    return wrapped


def enterprise_required_page(view):
    """Gates the Analysis Dashboard (StradIT's own competitor-intelligence
    tool) to Enterprise-tier accounts and admins - Individual/Small/Medium
    tiers don't get it. Must be applied together with (inside)
    @login_required_page, so the onboarding gate still runs first."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = get_current_user_id()
        user = get_user_by_id(user_id) if user_id else None
        if not user or not (getattr(user, "is_admin", False) or user.account_type == "enterprise"):
            return redirect(url_for("upgrade_required_page"))
        return view(*args, **kwargs)

    return wrapped
