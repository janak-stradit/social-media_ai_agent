"""
Database layer – synchronous SQLAlchemy + psycopg2.
Schema: social_media_agent
Tables: users, run_history
"""

# pylint: disable=not-callable,assignment-from-no-return
# SQLAlchemy's `func` proxy (func.count/func.coalesce/func.sum/...) is dynamic;
# pylint's static analysis can't see through it and misreports these two checks
# throughout this file. Known false positive, not scoped per-line for readability.

import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    or_,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.sql import func

from config import Config

logger = logging.getLogger(__name__)


def _utcnow():
    return datetime.now(timezone.utc)


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    try:
        # Only used to test driver availability.
        import psycopg2  # noqa: F401  pylint: disable=unused-import

        DATABASE_URL = "postgresql+psycopg2://postgres:root@localhost:5432/postgres"
    except ImportError:
        DATABASE_URL = "sqlite:///social_media_agent.db"

IS_SQLITE = DATABASE_URL.startswith("sqlite")
SCHEMA = "social_media_agent"

# Connections per process: enough for every web thread (gunicorn.conf.py
# GUNICORN_THREADS, 16) plus background threads. 2 web workers + the
# scheduler stay well under a small RDS instance's connection limit.
_POOL = {} if IS_SQLITE else {"pool_size": int(os.getenv("DB_POOL_SIZE", "16")),
                              "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "8")), "pool_recycle": 1800}

engine = create_engine(
    DATABASE_URL,
    **_POOL,
    pool_pre_ping=not IS_SQLITE,
    connect_args={"check_same_thread": False} if IS_SQLITE else {},
    echo=False,
)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    credit_limit: Mapped[float] = mapped_column(Float, default=lambda: Config.DEFAULT_CREDIT_USD, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Post-signup onboarding journey (see docs/plan for "Post-Signup Onboarding
    # Journey"): verify email -> pick account_type -> capture website (self-serve
    # tiers) or route to Contact Sales (enterprise).
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verification_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    verification_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    account_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # individual/small/medium/enterprise
    company_website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Separate from onboarding_completed: an Enterprise signup finishes onboarding
    # by submitting the Contact Sales form, but still shouldn't get self-serve
    # dashboard access until sales manually activates them (or an admin
    # deactivates any account later, e.g. for abuse) - login_required_page checks
    # this after onboarding_completed. Individual/Small/Medium default active.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Raised to end every signed-in session of the account at once (password
    # reset, two-step sign-in changes, "Sign out everywhere", admin deactivation):
    # each session cookie carries the version it started with (auth/utils.py).
    session_version: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)

    # Forgot-password flow (auth/routes.py forgot_password/reset_password).
    # Only a SHA-256 hash of the emailed token is stored, so a leaked row
    # can't be turned into a working reset link; cleared once used.
    password_reset_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    password_reset_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Website brand-analysis scan state (services/brand_profile_service.py).
    # Kept on the user rather than UserBrandProfile because a first scan has
    # no profile row yet. status: running / ready / failed; error is a
    # website_scraper_service.FAILURE_REASONS code (or "analysis_failed").
    brand_scan_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    brand_scan_error: Mapped[str | None] = mapped_column(String(32), nullable=True)
    brand_scan_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Who this user's "Send for Approval" requests go to (set on the Brand
    # Configuration pages); falls back to Config.APPROVAL_NOTIFY_EMAIL.
    approval_reviewer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Image access (Admin -> Users -> Image access): daily image limit
    # (None = the default from Image Settings, -1 = unlimited) and image model
    # (None = the default model).
    image_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Video is "coming soon" for everyone until an admin switches it on for
    # the user (Admin -> Users -> Image access -> Video). Admins always have it.
    video_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    # Weekly "ideas for you" email (services/idea_digest_service.py). None = on:
    # users get it until they switch it off (Settings -> Notifications, or the
    # email's unsubscribe link).
    ideas_email_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ideas_email_last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Timely nudges by email (services/nudge_service.py): an occasion coming
    # up, a trending topic, a reminder after a quiet spell. None = on. The
    # same nudges always show under the header bell, whatever this is.
    nudge_email_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Monthly recap email (services/recap_service.py). None = on.
    # recap_email_last_month: the month ("2026-09") the last recap covered, so each month is sent once.
    recap_email_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    recap_email_last_month: Mapped[str | None] = mapped_column(String(7), nullable=True)
    # Two-step sign-in (auth/two_factor.py): the authenticator secret,
    # encrypted; set while being set up, turned on once a code is confirmed.
    totp_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    totp_last_counter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Pricing plan (Plan.slug; see PRICING.md). None = the default for the
    # account type (plan_slug_for). plan_started_at anchors the monthly
    # allowance: it resets on that day of each month.
    plan_slug: Mapped[str | None] = mapped_column(String(32), nullable=True)
    plan_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Paid-for plans end here (then the user is back on the default plan);
    # None = no end (assigned by an admin, or the default plan).
    plan_paid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Billing details printed on GST invoices (Settings -> Plan & usage)
    billing_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    billing_gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    billing_address: Mapped[str | None] = mapped_column(String(400), nullable=True)
    billing_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Posts the user aims to create each week (Content Calendar). None = WEEKLY_POST_GOAL_DEFAULT.
    weekly_post_goal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # In-app guide (services/guide_service.py): JSON {"tours": [...seen],
    # "checklist_dismissed": bool, "events": [...]} - so tours don't repeat
    # on another device.
    guide_state: Mapped[str | None] = mapped_column(Text, nullable=True)


class SalesContactRequest(Base):
    """An Enterprise-tier signup's "Contact Sales" submission - see
    api/routes.py's /api/onboarding/contact-sales."""

    __tablename__ = "sales_contact_requests"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA}.users.id" if not IS_SQLITE else "users.id"), nullable=False, index=True
    )
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class UserBrandProfile(Base):
    """Per-user brand context derived from their onboarding website (see
    services/website_scraper_service.py + agents/website_analysis_agent.py) -
    the multi-tenant equivalent of the StradIT-only Content Guidelines
    (AppSetting). Read by services/brand_profile_service.py and folded into
    Studio Chat generation prompts."""

    __tablename__ = "user_brand_profiles"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA}.users.id" if not IS_SQLITE else "users.id"), unique=True, nullable=False, index=True
    )
    website: Mapped[str] = mapped_column(String(500), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_audience: Mapped[str | None] = mapped_column(String(500), nullable=True)
    brand_voice_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_themes: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    primary_colors: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of hex strings
    content_dos: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    content_donts: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    core_products: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    suggested_post_ideas: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of {category,title,summary,prompt}
    tagline: Mapped[str | None] = mapped_column(String(255), nullable=True)
    visual_style: Mapped[str | None] = mapped_column(Text, nullable=True)
    fonts: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of font-family names
    logo_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # ISO code the user picked on the brand profile page; sets the composer's
    # post languages (services/language_service.COUNTRIES)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Facts read straight off the site (website_scraper_service) - inputs to
    # the industry/region compliance layer. All JSON.
    schema_types: Mapped[str | None] = mapped_column(Text, nullable=True)  # schema.org @types the site declares
    social_links: Mapped[str | None] = mapped_column(Text, nullable=True)  # {platform: url}
    legal_pages: Mapped[str | None] = mapped_column(Text, nullable=True)  # [{type, url}]
    region_signals: Mapped[str | None] = mapped_column(Text, nullable=True)  # evidence dict
    regions_detected: Mapped[str | None] = mapped_column(Text, nullable=True)  # ["US", "UAE/GCC", "India"] subset
    site_disclaimers: Mapped[str | None] = mapped_column(Text, nullable=True)  # verbatim sentences
    certifications: Mapped[str | None] = mapped_column(Text, nullable=True)  # e.g. ["ISO 27001", "SOC 2"]
    # Compliance profile (services/compliance_rules.py). industry_category and
    # compliance_regions are what rules are selected by: the detected values
    # until the user confirms them, then the user's choice - a re-scan updates
    # industry_category_detected but never overwrites a confirmed choice.
    industry_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    industry_category_detected: Mapped[str | None] = mapped_column(String(64), nullable=True)
    compliance_regions: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list, subset of REGIONS
    compliance_excluded_rules: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of rule ids
    compliance_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class CreditRequest(Base):
    __tablename__ = "credit_requests"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA}.users.id" if not IS_SQLITE else "users.id"), nullable=False, index=True
    )
    requested_amount: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)  # pending, approved, rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class ImageGeneration(Base):
    """One row per generated or edited image - counts against the user's daily
    image limit (get_image_quota). Its cost is charged to the run when there is
    one (add_run_cost); rows with run_id NULL (e.g. Analysis Dashboard images)
    are charged here and summed into the user's used credits and admin totals."""

    __tablename__ = "image_generations"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA}.users.id" if not IS_SQLITE else "users.id"), nullable=False, index=True
    )
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="image", nullable=False)  # image | edit | video
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    media_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False, index=True)
    # Image versions (see image_lineage): parent = the image this one was edited
    # from, root = the original of the lineage, version = 1 for an original.
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    root_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    edit_instruction: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    clean_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Image Library: starred (set on the original of an edit chain) and removed
    # from the library (set on every version; the files stay, so posts that
    # already use them keep working)
    favorite_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)


class UserInvitation(Base):
    """An admin's email invitation to join (Admin -> Invitations). The emailed
    link carries `token`; signing up through it verifies the email right away."""

    __tablename__ = "user_invitations"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    token: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    invited_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA}.users.id" if not IS_SQLITE else "users.id"), nullable=True
    )
    # The name the email is signed with ("Janak has invited you") - typed by the
    # admin; falls back to their account name.
    inviter_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Clicks on the emailed link (/i/<token>), link scanners left out - see
    # services/invite_outreach.looks_automated
    click_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_clicked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_clicked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EmailSuppression(Base):
    """Addresses that must not get invitation / outreach emails again: they
    unsubscribed (the link or the mail app's one-click button), or an admin
    added them. One row per lower-case email."""

    __tablename__ = "email_suppressions"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    reason: Mapped[str] = mapped_column(String(32), nullable=False, default="unsubscribed")
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)  # link / one-click / admin
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class InviteMessage(Base):
    """Every invitation email sent from Admin -> Invitations, exactly as sent:
    which kind (designed / note / preview), the subject and the full text, and
    whether it went out. Shown per person and in Sent invitations."""

    __tablename__ = "invite_messages"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    invitation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    style: Mapped[str] = mapped_column(String(16), nullable=False, default="designed")  # designed / note / preview
    subject: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")  # the plain-text version, footer included
    html: Mapped[str | None] = mapped_column(Text, nullable=True)  # the HTML version, as sent
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="sent")  # sent / failed
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False, index=True)


class LeadPreview(Base):
    """Posts made for one lead before they sign up (services/lead_preview_service):
    their website's brand profile and 3 draft posts, shown on a no-login page
    /p/<token>. Images are made only when someone opens that page. Signing up
    from it copies the brand profile and the drafts into the new account."""

    __tablename__ = "lead_previews"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending/running/ready/failed
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    profile: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: WebsiteAnalysisAgent output
    posts: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of draft posts
    first_line: Mapped[str | None] = mapped_column(Text, nullable=True)  # an opening line for the email
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_viewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    images_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    claimed_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class InviteBatch(Base):
    """One uploaded file of people to invite (Admin -> Invitations -> Contacts),
    kept so every contact can be traced back to where it came from."""

    __tablename__ = "invite_batches"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA}.users.id" if not IS_SQLITE else "users.id"), nullable=True
    )
    rows_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_added: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class InviteContact(Base):
    """A person on the invite list: one row per email (lower-case), filled from
    uploads or added by hand, and linked to the invitation sent to them.
    Their status (not invited / invited / joined / expired / failed) is worked
    out from that invitation and from whether an account exists."""

    __tablename__ = "invite_contacts"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(120), nullable=True)
    company_size: Mapped[str | None] = mapped_column(String(40), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Any other columns of the uploaded file, as JSON {header: value} (e.g. Tier, Confidence, Source URL)
    extra: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="upload", nullable=False)  # upload | manual
    batch_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    invitation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    invite_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_invited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class Conversation(Base):
    """A Studio Chat thread: the runs (one per message) sent between two
    clicks of "New Conversation". See ensure_conversation."""

    __tablename__ = "conversations"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA}.users.id" if not IS_SQLITE else "users.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False, index=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Pinned to the top of the Studio Chat sidebar (newest pin first); None = not pinned
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # The image "it" / "this" refers to (an ImageGeneration id) - the newest
    # image, or the version the user picked with "Refine this version"
    active_image_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


class RunHistory(Base):
    __tablename__ = "run_history"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA}.users.id" if not IS_SQLITE else "users.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    story: Mapped[str] = mapped_column(Text, nullable=False)
    tone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    platforms: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_used: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, default=0.0, nullable=True)
    is_archived: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)
    # The Studio Chat conversation this message belongs to (see Conversation)
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


class SocialAccount(Base):
    __tablename__ = "social_accounts"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA}.users.id" if not IS_SQLITE else "users.id"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)  # facebook, instagram, linkedin, youtube
    account_name: Mapped[str] = mapped_column(String(120), nullable=False)
    account_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )  # Page ID, IG ID, Author URN, or Channel ID
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)  # Required for YouTube offline access
    connection_type: Mapped[str] = mapped_column(String(32), default="direct", nullable=False)  # direct, mcp
    mcp_endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    mcp_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    mcp_tool_name: Mapped[str | None] = mapped_column(String(120), default="linkedin_publish_post", nullable=True)
    # How it was connected: "manual" (details typed in Settings) or "oauth"
    # (one-click sign-in, services/social_oauth_service.py), and when the
    # sign-in runs out (UTC; LinkedIn's last 60 days, a Page token doesn't).
    connected_via: Mapped[str | None] = mapped_column(String(16), nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default="connected", nullable=False
    )  # connected, disconnected, expired
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class OAuthPending(Base):
    """Accounts found by a one-click sign-in when there are several to choose
    from (services/social_oauth_service.py). Holds their tokens for a few
    minutes, on the server, until the user picks one."""

    __tablename__ = "oauth_pending"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA}.users.id" if not IS_SQLITE else "users.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of options
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class ApprovedAsset(Base):
    __tablename__ = "approved_assets"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA}.users.id" if not IS_SQLITE else "users.id"), nullable=True, index=True
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)  # 'text', 'image', 'video'
    content_data: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class CompetitorPost(Base):
    __tablename__ = "competitor_posts"
    __table_args__ = (
        UniqueConstraint("competitor", "platform", "post_url", name="uq_competitor_post"),
        {"schema": SCHEMA} if not IS_SQLITE else {},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    competitor: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    post_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(120), nullable=True)
    post_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    engagement_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scraped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class OpportunitySuggestion(Base):
    __tablename__ = "opportunity_suggestions"
    __table_args__ = (
        UniqueConstraint("category", "title", name="uq_opportunity_suggestion"),
        {"schema": SCHEMA} if not IS_SQLITE else {},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )  # 'unserved_theme' | 'domain_expansion'
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_accounts: Mapped[str | None] = mapped_column(Text, nullable=True)  # comma-separated competitor/account names
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class ContentCollection(Base):
    """A "Suggested Storyline": a group of semantically-similar competitor posts
    (see services/embedding_service.py), labeled by CollectionAgent. Deduped by
    the exact set of posts it contains, so re-running suggestions doesn't create
    duplicate rows for the same cluster."""

    __tablename__ = "content_collections"
    __table_args__ = (
        UniqueConstraint("post_urls_hash", name="uq_content_collection_posts"),
        {"schema": SCHEMA} if not IS_SQLITE else {},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_urls_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevance: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    competitors: Mapped[str | None] = mapped_column(Text, nullable=True)  # comma-separated
    platforms: Mapped[str | None] = mapped_column(Text, nullable=True)  # comma-separated
    post_urls: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list
    post_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class SuggestedStorylineSeen(Base):
    """Every post_urls_hash ever surfaced as a Suggested Storyline, kept
    forever (unlike ContentCollection, which is wiped and rebuilt on every
    "Suggest Storylines" click so the displayed list doesn't accumulate
    stale entries). Used only to detect and exclude storylines the user has
    already been shown before, even after their post composition drifts
    slightly (a new republish added/removed) - see
    api/routes.py's generate_suggested_collections."""

    __tablename__ = "suggested_storyline_seen"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    post_urls_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class Payment(Base):
    """One purchase through a payment provider (services/billing_service.py).
    The amount and what it buys are set by the server when the order is
    created; fulfilled_at makes applying it happen only once, however many
    times the browser and the webhook report it."""

    __tablename__ = "payments"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)  # stripe
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    item: Mapped[str] = mapped_column(String(48), nullable=False)  # plan:growth:month, pack:image10, ...
    description: Mapped[str | None] = mapped_column(String(160), nullable=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)  # smallest unit (paise), GST included
    tax: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="created", nullable=False)  # created / paid / failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refunded_amount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PublishedPost(Base):
    """A post the app published to a platform (scheduled or from an approved
    request), and how it is doing there (services/metrics_service.py).
    metrics: {likes, comments, shares, saves, reach, views} - whatever the
    platform reports; engagement = likes + comments + shares + saves."""

    __tablename__ = "published_posts"
    __table_args__ = (
        UniqueConstraint("platform", "platform_post_id", name="uq_published_platform_post"),
        {"schema": SCHEMA} if not IS_SQLITE else {},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    platform_post_id: Mapped[str] = mapped_column(String(160), nullable=False)
    permalink: Mapped[str | None] = mapped_column(String(500), nullable=True)
    title: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # scheduled / approval
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False, index=True)
    metrics: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    engagement: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metrics_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    metrics_error: Mapped[str | None] = mapped_column(String(300), nullable=True)


class Subscription(Base):
    """An automatically renewing plan (a Stripe subscription). Each charge is
    also a Payment, applied once, which extends the plan
    (db.extend_paid_plan) and gets its own invoice."""

    __tablename__ = "subscriptions"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_subscription_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    plan_slug: Mapped[str] = mapped_column(String(32), nullable=False)
    period: Mapped[str] = mapped_column(String(8), nullable=False)  # month / year
    amount: Mapped[int] = mapped_column(Integer, nullable=False)  # each charge, paise / cents (GST included)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    # created (waiting for the first payment) / active / cancelled (no more renewals) / ended
    status: Mapped[str] = mapped_column(String(16), default="created", nullable=False)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Invoice(Base):
    """A GST tax invoice for a payment, or a credit note for a refund
    (services/invoice_service.py). Amounts in paise / cents; the seller and
    buyer details are copied in, so a later change of address doesn't alter
    an issued invoice."""

    __tablename__ = "invoices"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)  # GST allows 16 characters
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # invoice / credit_note
    series: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # e.g. AVIR/26-27
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    original_number: Mapped[str | None] = mapped_column(String(16), nullable=True)  # credit note -> its invoice
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    taxable: Mapped[int] = mapped_column(Integer, nullable=False)
    cgst: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sgst: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    igst: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)  # JSON: seller, buyer, line, place of supply, notes


class PaymentEvent(Base):
    """Webhook events already handled (provider's event id), so a resent
    webhook is ignored."""

    __tablename__ = "payment_events"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    event_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class AuditLog(Base):
    """Who changed what, for changes that matter for money and access: plans,
    packs, credit, video access, account status, two-step sign-in, and (later)
    payments. actor_user_id is None for the system or a payment provider."""

    __tablename__ = "audit_log"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False, index=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    target_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: before / after / note
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Plan(Base):
    """A pricing plan (PRICING.md section 8). Seeded with DEFAULT_PLANS on
    first start; admins can change prices and limits later without a
    release. A limit of None means unlimited ("Custom" for Enterprise)."""

    __tablename__ = "plans"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    slug: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    price_inr: Mapped[float | None] = mapped_column(Float, nullable=True)  # monthly, before GST; None = on request
    price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    annual_inr: Mapped[float | None] = mapped_column(Float, nullable=True)
    annual_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_for: Mapped[str | None] = mapped_column(String(120), nullable=True)
    posts_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    images_month: Mapped[int | None] = mapped_column(Integer, nullable=True)  # includes edits
    images_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    videos_month: Mapped[int | None] = mapped_column(Integer, nullable=True)  # included videos; 0 = packs only
    videos_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_pack_discount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # percent
    brands: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approvals: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    support: Mapped[str | None] = mapped_column(String(64), nullable=True)


class UsagePack(Base):
    """Prepaid images or videos on top of the plan (PRICING.md top-up packs).
    Used only after the plan's monthly allowance runs out, oldest first;
    expire after 12 months. Granted by an admin until payments exist."""

    __tablename__ = "usage_packs"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # image / video
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # bought online: the payment


class IndustryTrend(Base):
    """This week's talked-about topics for one industry in one market (see
    services/trend_service.py). Shared by every user in that industry, so
    trends are looked up once a day per industry, not once per user."""

    __tablename__ = "industry_trends"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    key: Mapped[str] = mapped_column(String(160), primary_key=True)  # "<industry_category>|<region>"
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of {topic, summary, why_now, source_title, source_url}
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)  # google_search / google_news
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class UserIdeaFeed(Base):
    """Studio Chat's "Ideas for you" for one user: the industry's trends,
    post types and upcoming dates turned into briefs for their brand."""

    __tablename__ = "user_idea_feeds"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON, see trend_service.generate_idea_feed
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class IdeaLink(Base):
    """One idea sent in a weekly ideas email. The email's button opens
    /dashboard?idea=<token>, which loads this brief into the composer - the
    user's feed has usually moved on by the time they click."""

    __tablename__ = "idea_links"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON idea: title, prompt, platform, format, ...
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # first click


class MediaJob(Base):
    """An image or video being made in the background (services/media_jobs.py)."""

    __tablename__ = "media_jobs"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    media_type: Mapped[str] = mapped_column(String(10), nullable=False)
    platform: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="running")  # running | done | failed
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class Notification(Base):
    """One item under the header bell (services/nudge_service.py): an
    occasion coming up, a trending topic or a reminder, with a link that
    opens Studio Chat with an idea loaded. dedupe_key stops the same nudge
    being created twice for a user."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_notification_user_dedupe"),
        {"schema": SCHEMA} if not IS_SQLITE else {},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # occasion / trend / inactive
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)  # path, e.g. /dashboard?idea=<token>
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EmailLog(Base):
    """Every email the app tried to send (services/email_service.py logs it in
    one place), for Admin -> Emails. status: sent / failed. details: JSON with
    what the email was built from (industry, market, occasion, idea titles).
    triggered_by: "system" (scheduler, sign-up, approvals) or "admin:<id>"."""

    __tablename__ = "email_log"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    to_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_by: Mapped[str] = mapped_column(String(40), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False, index=True)


class AppSetting(Base):
    """Generic editable-text settings the user can update from the dashboard
    (e.g. Content Guidelines, Products & Service overview) - stored here
    instead of hardcoded in source, and read live by generation (see
    services/stradit_service.py, agents/story_agent.py) so edits actually
    change what gets generated."""

    __tablename__ = "app_settings"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class BrandAsset(Base):
    """A brand character or logo reference image, manageable from
    /brand-configuration's "Logo & Character" tab - an extensible list
    (add/replace/remove) rather than a fixed pair, read by dashboard.js's
    Character Setup checkboxes and by kie.ai image generation as a reference
    image."""

    __tablename__ = "brand_assets"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class ApprovalRequest(Base):
    """An out-of-band review request for a competitor-dashboard pipeline's
    generated content, sent by email as a link to the dashboard (a signed-in
    reviewer opens the link, sees the same platform preview, and accepts or
    rejects with a comment). pipeline_client_id is the pipeline's client-side
    id (a JS Date.now() timestamp) so the dashboard's localStorage-only
    pipeline history can be reconciled with the persisted decision."""

    __tablename__ = "approval_requests"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA}.users.id" if not IS_SQLITE else "users.id"), nullable=True, index=True
    )
    pipeline_client_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    story_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    competitors: Mapped[str | None] = mapped_column(Text, nullable=True)  # comma-separated
    image_urls: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending/approved/rejected
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Compliance review of the caption at request time (services/compliance_service.py) -
    # JSON {rules_checked, flags, disclaimers_added, needs_attention, suggested_caption}; null
    # when the requester has no compliance profile.
    compliance: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Where the "Review & Decide" email was sent - that reviewer (signed in
    # with this email) can see and decide the request alongside its owner.
    reviewer_email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # A whole post for several platforms (Studio Chat "Send for approval"),
    # JSON list - one entry per platform (services/approval_bundle_service.py):
    # {platform, caption, hashtags, media: none|single|carousel, images,
    #  slide_titles, linkedin_format, pdf_url, compliance, decision, comment}.
    # Null for the older one-platform requests (the columns above).
    items: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Reminder emails to the reviewer while it stays pending
    # (services/approval_reminder_service.py): how many were sent, and when
    # the last one was tried.
    reminder_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    last_reminder_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA}.users.id" if not IS_SQLITE else "users.id"), nullable=False, index=True
    )
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    platforms: Mapped[str] = mapped_column(Text, nullable=False)  # JSON or comma-separated string
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False
    )  # pending, publishing, published, partial, failed, cancelled (services/schedule_service.py)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class AutopilotSettings(Base):
    """A user's Autopilot set-up (services/autopilot_service.py): how often,
    where, what about, and the monthly image cap. JSON lists/dicts as text."""

    __tablename__ = "autopilot_settings"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    posts_per_week: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    platforms: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    mix: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    festivals: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    topics: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    avoid: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    days_off: Mapped[str] = mapped_column(Text, nullable=False, default="[6]")  # 0 = Monday
    images: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    image_cap_month: Mapped[int] = mapped_column(Integer, default=12, nullable=False)
    language: Mapped[str] = mapped_column(String(16), default="en", nullable=False)
    tz_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class AutopilotPost(Base):
    """One planned Autopilot post: planned -> needs_ok (written, waiting for the
    user) -> approved (on the calendar as a ScheduledPost) -> posted, or
    skipped / held (not approved in time) / failed."""

    __tablename__ = "autopilot_posts"
    __table_args__ = (
        UniqueConstraint("user_id", "slot_at", "platform", name="uq_autopilot_slot"),
        {"schema": SCHEMA} if not IS_SQLITE else {},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    slot_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)  # UTC
    platform: Mapped[str] = mapped_column(String(24), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # tips / offers / news / festival
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    topic: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="planned", nullable=False, index=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashtags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    checks: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    approve_by: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scheduled_post_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class SavedBrief(Base):
    """A brief the user starred in Studio Chat to use again (the text they
    typed, and the platforms it was for)."""

    __tablename__ = "saved_briefs"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA}.users.id" if not IS_SQLITE else "users.id"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    platforms: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class MemoryEmbedding(Base):
    __tablename__ = "memory_embeddings"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=True)
    embedding_array: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    # Whose memory it is (0: not tied to a user). Searched in the database, so
    # a generation reads only its own user's memories (services/memory_service.py).
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


class CompetitorPostEmbedding(Base):
    __tablename__ = "competitor_post_embeddings"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[str] = mapped_column(String(1000), primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=True)
    embedding_array: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


def init_db():
    """Create schema, tables, and apply lightweight migrations."""
    if not IS_SQLITE:
        with engine.connect() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
            conn.commit()

    Base.metadata.create_all(engine)
    _seed_plans()

    with engine.connect() as conn:
        run_tbl = f'"{SCHEMA}".run_history' if not IS_SQLITE else "run_history"
        img_tbl = f'"{SCHEMA}".image_generations' if not IS_SQLITE else "image_generations"
        usr_tbl = f'"{SCHEMA}".users' if not IS_SQLITE else "users"

        def _tbl(name: str) -> str:
            return f'"{SCHEMA}".{name}' if not IS_SQLITE else name

        for alter_cmd in [
            f"ALTER TABLE {run_tbl} ADD COLUMN user_id INTEGER",
            f"ALTER TABLE {run_tbl} ADD COLUMN tokens_used INTEGER DEFAULT 0",
            f"ALTER TABLE {run_tbl} ADD COLUMN cost_usd DOUBLE PRECISION DEFAULT 0.0",
            f"ALTER TABLE {run_tbl} ADD COLUMN is_archived BOOLEAN DEFAULT FALSE",
            f"ALTER TABLE {run_tbl} ADD COLUMN conversation_id INTEGER",
            f"CREATE INDEX IF NOT EXISTS ix_run_history_conversation_id ON {run_tbl} (conversation_id)",
            f"ALTER TABLE {_tbl('conversations')} ADD COLUMN pinned_at TIMESTAMP",
            f"ALTER TABLE {img_tbl} ADD COLUMN conversation_id INTEGER",
            f"ALTER TABLE {img_tbl} ADD COLUMN parent_id INTEGER",
            f"ALTER TABLE {img_tbl} ADD COLUMN root_id INTEGER",
            f"ALTER TABLE {img_tbl} ADD COLUMN version INTEGER",
            f"ALTER TABLE {img_tbl} ADD COLUMN prompt TEXT",
            f"ALTER TABLE {img_tbl} ADD COLUMN edit_instruction VARCHAR(1000)",
            f"ALTER TABLE {img_tbl} ADD COLUMN clean_url VARCHAR(1000)",
            f"ALTER TABLE {img_tbl} ADD COLUMN width INTEGER",
            f"ALTER TABLE {img_tbl} ADD COLUMN height INTEGER",
            f"ALTER TABLE {img_tbl} ADD COLUMN favorite_at TIMESTAMP",
            f"ALTER TABLE {img_tbl} ADD COLUMN deleted_at TIMESTAMP",
            f"CREATE INDEX IF NOT EXISTS ix_image_generations_conversation_id ON {img_tbl} (conversation_id)",
            f"CREATE INDEX IF NOT EXISTS ix_image_generations_parent_id ON {img_tbl} (parent_id)",
            f"CREATE INDEX IF NOT EXISTS ix_image_generations_root_id ON {img_tbl} (root_id)",
            f"ALTER TABLE {usr_tbl} ADD COLUMN credit_limit DOUBLE PRECISION DEFAULT {Config.DEFAULT_CREDIT_USD}",
            f"ALTER TABLE {usr_tbl} ADD COLUMN is_admin BOOLEAN DEFAULT FALSE",
            f"ALTER TABLE {usr_tbl} ADD COLUMN email_verified BOOLEAN DEFAULT FALSE",
            f"ALTER TABLE {usr_tbl} ADD COLUMN verification_token VARCHAR(64)",
            f"ALTER TABLE {usr_tbl} ADD COLUMN verification_sent_at TIMESTAMP",
            f"ALTER TABLE {usr_tbl} ADD COLUMN account_type VARCHAR(32)",
            f"ALTER TABLE {usr_tbl} ADD COLUMN company_website VARCHAR(500)",
            f"ALTER TABLE {usr_tbl} ADD COLUMN onboarding_completed BOOLEAN DEFAULT FALSE",
            f"ALTER TABLE {usr_tbl} ADD COLUMN is_active BOOLEAN DEFAULT TRUE",
            f"ALTER TABLE {usr_tbl} ADD COLUMN session_version INTEGER DEFAULT 0",
            f"ALTER TABLE {usr_tbl} ADD COLUMN password_reset_token_hash VARCHAR(64)",
            f"ALTER TABLE {usr_tbl} ADD COLUMN password_reset_sent_at TIMESTAMP",
            f"ALTER TABLE {usr_tbl} ADD COLUMN brand_scan_status VARCHAR(16)",
            f"ALTER TABLE {usr_tbl} ADD COLUMN brand_scan_error VARCHAR(32)",
            f"ALTER TABLE {usr_tbl} ADD COLUMN brand_scan_updated_at TIMESTAMP",
            f"ALTER TABLE {usr_tbl} ADD COLUMN approval_reviewer_email VARCHAR(255)",
            f"ALTER TABLE {usr_tbl} ADD COLUMN image_limit INTEGER",
            f"ALTER TABLE {usr_tbl} ADD COLUMN image_model VARCHAR(128)",
            f"ALTER TABLE {usr_tbl} ADD COLUMN video_enabled BOOLEAN DEFAULT FALSE",
            f"ALTER TABLE {usr_tbl} ADD COLUMN ideas_email_enabled BOOLEAN",
            f"ALTER TABLE {usr_tbl} ADD COLUMN ideas_email_last_sent_at TIMESTAMP",
            f"ALTER TABLE {usr_tbl} ADD COLUMN nudge_email_enabled BOOLEAN",
            f"ALTER TABLE {usr_tbl} ADD COLUMN weekly_post_goal INTEGER",
            f"ALTER TABLE {usr_tbl} ADD COLUMN plan_slug VARCHAR(32)",
            f"ALTER TABLE {usr_tbl} ADD COLUMN totp_secret TEXT",
            f"ALTER TABLE {usr_tbl} ADD COLUMN totp_enabled_at TIMESTAMP",
            f"ALTER TABLE {usr_tbl} ADD COLUMN totp_last_counter INTEGER",
            f"ALTER TABLE {usr_tbl} ADD COLUMN plan_started_at TIMESTAMP",
            f"ALTER TABLE {usr_tbl} ADD COLUMN plan_paid_until TIMESTAMP",
            f"ALTER TABLE {usr_tbl} ADD COLUMN billing_name VARCHAR(160)",
            f"ALTER TABLE {usr_tbl} ADD COLUMN billing_gstin VARCHAR(15)",
            f"ALTER TABLE {usr_tbl} ADD COLUMN billing_address VARCHAR(400)",
            f"ALTER TABLE {usr_tbl} ADD COLUMN billing_state VARCHAR(64)",
            f"ALTER TABLE {_tbl('payments')} ADD COLUMN refunded_amount INTEGER DEFAULT 0",
            f"ALTER TABLE {_tbl('payments')} ADD COLUMN refunded_at TIMESTAMP",
            f"ALTER TABLE {_tbl('usage_packs')} ADD COLUMN order_id VARCHAR(64)",
            f"ALTER TABLE {usr_tbl} ADD COLUMN guide_state TEXT",
            f"ALTER TABLE {usr_tbl} ADD COLUMN recap_email_enabled BOOLEAN",
            f"ALTER TABLE {usr_tbl} ADD COLUMN recap_email_last_month VARCHAR(7)",
            f"ALTER TABLE {_tbl('memory_embeddings')} ADD COLUMN user_id INTEGER",
            f"CREATE INDEX IF NOT EXISTS ix_memory_embeddings_user_id ON {_tbl('memory_embeddings')} (user_id)",
        ]:
            try:
                with engine.begin() as sub_conn:
                    sub_conn.execute(text(alter_cmd))
            except Exception:
                pass

        profile_tbl = f'"{SCHEMA}".user_brand_profiles' if not IS_SQLITE else "user_brand_profiles"
        approval_tbl = f'"{SCHEMA}".approval_requests' if not IS_SQLITE else "approval_requests"
        invite_tbl = f'"{SCHEMA}".user_invitations' if not IS_SQLITE else "user_invitations"
        for alter_cmd in [
            f"ALTER TABLE {profile_tbl} ADD COLUMN company_name VARCHAR(255)",
            f"ALTER TABLE {profile_tbl} ADD COLUMN suggested_post_ideas TEXT",
            f"ALTER TABLE {profile_tbl} ADD COLUMN tagline VARCHAR(255)",
            f"ALTER TABLE {profile_tbl} ADD COLUMN visual_style TEXT",
            f"ALTER TABLE {profile_tbl} ADD COLUMN fonts TEXT",
            f"ALTER TABLE {profile_tbl} ADD COLUMN logo_url VARCHAR(1000)",
            f"ALTER TABLE {profile_tbl} ADD COLUMN core_products TEXT",
            f"ALTER TABLE {profile_tbl} ADD COLUMN schema_types TEXT",
            f"ALTER TABLE {profile_tbl} ADD COLUMN social_links TEXT",
            f"ALTER TABLE {profile_tbl} ADD COLUMN legal_pages TEXT",
            f"ALTER TABLE {profile_tbl} ADD COLUMN region_signals TEXT",
            f"ALTER TABLE {profile_tbl} ADD COLUMN regions_detected TEXT",
            f"ALTER TABLE {profile_tbl} ADD COLUMN site_disclaimers TEXT",
            f"ALTER TABLE {profile_tbl} ADD COLUMN certifications TEXT",
            f"ALTER TABLE {profile_tbl} ADD COLUMN industry_category VARCHAR(64)",
            f"ALTER TABLE {profile_tbl} ADD COLUMN industry_category_detected VARCHAR(64)",
            f"ALTER TABLE {profile_tbl} ADD COLUMN compliance_regions TEXT",
            f"ALTER TABLE {profile_tbl} ADD COLUMN compliance_excluded_rules TEXT",
            f"ALTER TABLE {profile_tbl} ADD COLUMN compliance_confirmed_at TIMESTAMP",
            f"ALTER TABLE {profile_tbl} ADD COLUMN country VARCHAR(8)",
            f"ALTER TABLE {approval_tbl} ADD COLUMN compliance TEXT",
            f"ALTER TABLE {approval_tbl} ADD COLUMN reviewer_email VARCHAR(255)",
            f"ALTER TABLE {approval_tbl} ADD COLUMN items TEXT",
            f"ALTER TABLE {approval_tbl} ADD COLUMN reminder_count INTEGER DEFAULT 0",
            f"ALTER TABLE {approval_tbl} ADD COLUMN last_reminder_at TIMESTAMP",
            f"ALTER TABLE {invite_tbl} ADD COLUMN inviter_name VARCHAR(255)",
        ]:
            try:
                with engine.begin() as sub_conn:
                    sub_conn.execute(text(alter_cmd))
            except Exception:
                pass

        # Invitations: link clicks (outreach funnel)
        inv_tbl = f'"{SCHEMA}".user_invitations' if not IS_SQLITE else "user_invitations"
        for col_name, col_type in [("click_count", "INTEGER NOT NULL DEFAULT 0"), ("first_clicked_at", "TIMESTAMP"),
                                   ("last_clicked_at", "TIMESTAMP")]:
            try:
                with engine.begin() as sub_conn:
                    sub_conn.execute(text(f"ALTER TABLE {inv_tbl} ADD COLUMN {col_name} {col_type}"))
            except Exception:
                pass

        # Invitation emails: the HTML version, kept since it was added
        msg_tbl = f'"{SCHEMA}".invite_messages' if not IS_SQLITE else "invite_messages"
        try:
            with engine.begin() as sub_conn:
                sub_conn.execute(text(f"ALTER TABLE {msg_tbl} ADD COLUMN html TEXT"))
        except Exception:  # noqa: BLE001 - already there
            pass

        # Invite contacts: the lead-sheet columns added after the table was first created
        contact_tbl = f'"{SCHEMA}".invite_contacts' if not IS_SQLITE else "invite_contacts"
        for col_name, col_type in [("city", "VARCHAR(80)"), ("industry", "VARCHAR(120)"), ("company_size", "VARCHAR(40)"),
                                   ("website", "VARCHAR(255)"), ("description", "TEXT"), ("extra", "TEXT")]:
            try:
                with engine.begin() as sub_conn:
                    sub_conn.execute(text(f"ALTER TABLE {contact_tbl} ADD COLUMN {col_name} {col_type}"))
            except Exception:
                pass

        soc_tbl = f'"{SCHEMA}".social_accounts' if not IS_SQLITE else "social_accounts"
        for col_name, col_type in [
            ("connection_type", "VARCHAR(32) DEFAULT 'direct'"),
            ("mcp_endpoint", "TEXT"),
            ("mcp_token", "TEXT"),
            ("mcp_tool_name", "VARCHAR(120) DEFAULT 'linkedin_publish_post'"),
            ("refresh_token", "TEXT"),
            ("connected_via", "VARCHAR(16)"),
            ("token_expires_at", "TIMESTAMP"),
        ]:
            try:
                with engine.begin() as sub_conn:
                    sub_conn.execute(text(f"ALTER TABLE {soc_tbl} ADD COLUMN {col_name} {col_type}"))
            except Exception:
                pass

        # Some source URLs (e.g. Google News RSS redirects) exceed the original
        # VARCHAR(500) post_url limit and silently fail the whole insert batch.
        if not IS_SQLITE:
            comp_post_tbl = f'"{SCHEMA}".competitor_posts'
            try:
                with engine.begin() as sub_conn:
                    sub_conn.execute(text(f"ALTER TABLE {comp_post_tbl} ALTER COLUMN post_url TYPE VARCHAR(1000)"))
            except Exception:
                pass

        conn.commit()

    # Seed default admin if no admin user exists
    try:
        from werkzeug.security import generate_password_hash

        with Session(engine) as session:
            admin_user = session.query(User).filter(User.is_admin.is_(True)).first()
            if not admin_user:
                # Check if admin email exists
                existing = (
                    session.query(User)
                    .filter((User.email == "admin@vortexsocial.ai") | (User.email == "admin@contentai.com"))
                    .first()
                )
                if existing:
                    existing.is_admin = True  # type: ignore[assignment]
                    existing.credit_limit = max(existing.credit_limit or 0.0, 1000.0)  # type: ignore[assignment]
                else:
                    # Never a known password: ADMIN_PASSWORD if set, else a random one
                    # shown once in the log (sign in, then change it)
                    password = os.getenv("ADMIN_PASSWORD") or secrets.token_urlsafe(16)
                    new_admin = User(
                        name="System Admin",
                        email=os.getenv("ADMIN_EMAIL") or "admin@vortexsocial.ai",
                        password_hash=generate_password_hash(password),
                        credit_limit=1000.0,
                        is_admin=True,
                    )
                    session.add(new_admin)
                    if not os.getenv("ADMIN_PASSWORD"):
                        logger.warning(f"First admin created: {new_admin.email} with password {password} - change it after signing in.")
                session.commit()
    except Exception as seed_err:
        logger.warning(f"Warning seeding admin user: {seed_err}")

    # Older versions created the first admin with the password "admin123"
    try:
        from werkzeug.security import check_password_hash

        with Session(engine) as session:
            for admin in session.query(User).filter(User.is_admin.is_(True)).all():
                if admin.password_hash and check_password_hash(admin.password_hash, "admin123"):
                    logger.error(f"SECURITY: admin {admin.email} still uses the old default password - change it now.")
    except Exception as check_err:
        logger.warning(f"Could not check admin passwords: {check_err}")

    try:
        filled = _backfill_memory_owners()
        if filled:
            logger.info(f"Set the owner of {filled} stored memor{'y' if filled == 1 else 'ies'}.")
    except Exception as mem_err:  # noqa: BLE001 - start-up must not fail on it
        logger.warning(f"Warning backfilling memory owners: {mem_err}")

    try:
        made = _backfill_conversations()
        if made:
            logger.info(f"Grouped {made} earlier run(s) into conversations.")
    except Exception as conv_err:  # noqa: BLE001 - start-up must not fail on it
        logger.warning(f"Warning backfilling conversations: {conv_err}")


def _backfill_memory_owners() -> int:
    """Memories saved before memory_embeddings.user_id existed get it from their
    metadata (0 when they belong to no user). Runs once per memory."""
    import json as _json

    filled = 0
    with Session(engine) as session:
        while True:
            rows = session.query(MemoryEmbedding).filter(MemoryEmbedding.user_id.is_(None)).limit(500).all()
            if not rows:
                break
            for row in rows:
                try:
                    owner = str((_json.loads(row.metadata_json or "{}") or {}).get("user_id") or "")
                except (ValueError, TypeError, AttributeError):
                    owner = ""
                row.user_id = int(owner) if owner.isdigit() else 0
                filled += 1
            session.commit()
    return filled


def _serialize_run(row: RunHistory, truncate_story: bool = False) -> dict:
    story = row.story if row.story else ""
    if truncate_story and len(story) > 120:
        story = story[:120] + "..."
    return {
        "id": row.id,
        "user_id": row.user_id,
        "timestamp": row.created_at.strftime("%Y-%m-%d %H:%M" if truncate_story else "%Y-%m-%d %H:%M:%S"),
        "story": story,
        "tone": row.tone,
        "platforms": json.loads(row.platforms) if row.platforms else [],
        "content": json.loads(row.content) if row.content else {},
        "tokens_used": row.tokens_used or 0,
        "cost_usd": round(float(row.cost_usd or 0.0), 6),  # type: ignore
        "is_archived": bool(getattr(row, "is_archived", False)),
        "conversation_id": getattr(row, "conversation_id", None),
    }


def create_user(name: str, email: str, password_hash: str, is_admin: bool = False, credit_limit: float | None = None) -> dict:
    with Session(engine) as session:
        row = User(
            name=name.strip(),
            email=email.strip().lower(),
            password_hash=password_hash,
            is_admin=is_admin,
            credit_limit=Config.DEFAULT_CREDIT_USD if credit_limit is None else credit_limit,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return {
            "id": row.id,
            "name": row.name,
            "email": row.email,
            "is_admin": row.is_admin,
            "credit_limit": row.credit_limit,
        }


def get_user_by_email(email: str) -> User | None:
    with Session(engine) as session:
        return session.query(User).filter(User.email == email.strip().lower()).first()


def get_user_by_id(user_id: int) -> User | None:
    with Session(engine) as session:
        return session.get(User, user_id)


def set_user_verification_token(user_id: int, token: str) -> None:
    """(Re)issues a verification token - used both at registration and by the
    resend-verification endpoint."""
    with Session(engine) as session:
        session.query(User).filter(User.id == user_id).update(
            {"verification_token": token, "verification_sent_at": _utcnow()}
        )
        session.commit()


def get_user_by_verification_token(token: str) -> User | None:
    with Session(engine) as session:
        return session.query(User).filter(User.verification_token == token).first()


def set_user_password_reset_token(user_id: int, token_hash: str) -> None:
    """Stores the hash of a freshly issued password-reset token (replacing
    any earlier one, so only the newest emailed link works)."""
    with Session(engine) as session:
        session.query(User).filter(User.id == user_id).update(
            {"password_reset_token_hash": token_hash, "password_reset_sent_at": _utcnow()}
        )
        session.commit()


def get_user_by_password_reset_token_hash(token_hash: str) -> User | None:
    with Session(engine) as session:
        return session.query(User).filter(User.password_reset_token_hash == token_hash).first()


def reset_user_password(user_id: int, password_hash: str) -> None:
    """Sets the new password and clears the reset token so the link is
    single-use."""
    with Session(engine) as session:
        session.query(User).filter(User.id == user_id).update(
            {"password_hash": password_hash, "password_reset_token_hash": None, "password_reset_sent_at": None}
        )
        session.commit()


def mark_user_email_verified(user_id: int) -> None:
    with Session(engine) as session:
        session.query(User).filter(User.id == user_id).update(
            {"email_verified": True, "verification_token": None}
        )
        session.commit()


def complete_user_onboarding(user_id: int, account_type: str, company_website: str | None = None) -> None:
    """account_type is 'individual'/'small'/'medium': sets onboarding_completed
    so login_required_page lets the user through to the dashboard. Enterprise is
    NOT completed here - see complete_enterprise_contact_sales, which is what
    actually ends that path (after they submit the sales form)."""
    with Session(engine) as session:
        session.query(User).filter(User.id == user_id).update(
            {
                "account_type": account_type,
                "company_website": company_website,
                "onboarding_completed": True,
            }
        )
        session.commit()


def set_user_account_type_enterprise(user_id: int) -> None:
    """Records the Enterprise selection without completing onboarding -
    login_required_page keeps routing the user to /onboarding/contact-sales
    until complete_enterprise_contact_sales runs."""
    with Session(engine) as session:
        session.query(User).filter(User.id == user_id).update({"account_type": "enterprise"})
        session.commit()


def create_sales_contact_request(user_id: int, company_name: str, phone: str | None, message: str | None) -> dict:
    """Saves the Enterprise "Contact Sales" submission and marks onboarding
    complete, but leaves is_active False - this ends their onboarding FUNNEL,
    not their access gate. They stay on a "pending activation" page until an
    admin flips is_active (see set_user_active), presumably once sales has
    manually set up their account."""
    with Session(engine) as session:
        row = SalesContactRequest(
            user_id=user_id,
            company_name=company_name.strip(),
            phone=(phone or "").strip() or None,
            message=(message or "").strip() or None,
        )
        session.add(row)
        session.query(User).filter(User.id == user_id).update({"onboarding_completed": True, "is_active": False})
        session.commit()
        session.refresh(row)
        return {"id": row.id, "company_name": row.company_name, "phone": row.phone, "message": row.message}


def get_session_version(user_id: int) -> int | None:
    """The account's current session version, or None when the account is gone."""
    with Session(engine) as session:
        row = session.query(User.session_version).filter(User.id == user_id).first()
        return None if row is None else int(row[0] or 0)


def bump_session_version(user_id: int) -> int:
    """Ends every signed-in session of the account. Returns the new version."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            return 0
        user.session_version = int(user.session_version or 0) + 1
        session.commit()
        return user.session_version


def set_user_active(user_id: int, is_active: bool) -> dict | None:
    """Admin action: activate/deactivate a user's access. See
    api/routes.py's /api/admin/users/<id>/active."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            return None
        user.is_active = is_active
        session.commit()
        return {"id": user.id, "is_active": user.is_active}


# Every table that carries a user_id FK - see api/routes.py's
# admin_hard_delete_user for why this can't just be "every table": global
# StradIT data (competitor_posts, content_collections, app_settings, etc.)
# has no user association and must never be touched by a per-user delete.
_USER_OWNED_TABLES = (
    SalesContactRequest,
    UserBrandProfile,
    CreditRequest,
    RunHistory,
    Conversation,
    SocialAccount,
    ApprovedAsset,
    ApprovalRequest,
    ScheduledPost,
    ImageGeneration,
)


def hard_delete_user(user_id: int) -> dict | None:
    """Permanently deletes a user AND every row of their data across all
    user-owned tables, in one transaction (all-or-nothing). Irreversible -
    see api/routes.py's admin_hard_delete_user for the admin-only gating,
    self-delete/admin-target guards, and the confirmation this requires on
    the frontend (templates/admin.html). Returns None if the user doesn't
    exist; otherwise a dict of {table_name: rows_deleted} plus the deleted
    user's id/email, for the admin audit toast."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            return None

        deleted_counts = {}
        for model in _USER_OWNED_TABLES:
            result = session.query(model).filter(model.user_id == user_id).delete()
            deleted_counts[model.__tablename__] = result

        email = user.email
        session.delete(user)
        session.commit()

        return {"id": user_id, "email": email, "deleted": deleted_counts}


ACCOUNT_TYPES = ("individual", "small", "medium", "enterprise")


def update_user_profile(
    user_id: int,
    name: str | None = None,
    email: str | None = None,
    account_type: str | None = None,
    company_website: str | None = None,
) -> dict | None:
    """Admin action: edit a user's name/email/account type/website. See
    api/routes.py's /api/admin/users/<id>/profile. account_type/
    company_website are admin overrides of what onboarding captured -
    setting account_type here does not touch onboarding_completed/is_active
    (unlike complete_user_onboarding/set_user_account_type_enterprise, which
    run as part of the onboarding funnel itself)."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            return None
        if name:
            user.name = name.strip()
        if email:
            normalized_email = email.strip().lower()
            existing = session.query(User).filter(User.email == normalized_email, User.id != user_id).first()
            if existing:
                raise ValueError("Another account already uses that email")
            user.email = normalized_email
        if account_type:
            if account_type not in ACCOUNT_TYPES:
                raise ValueError(f"Invalid account_type - must be one of {', '.join(ACCOUNT_TYPES)}")
            user.account_type = account_type
        if company_website is not None:
            user.company_website = company_website.strip() or None
        session.commit()
        return {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "account_type": user.account_type,
            "company_website": user.company_website,
        }


def save_user_brand_profile(
    user_id: int,
    website: str,
    company_name: str | None,
    industry: str | None,
    target_audience: str | None,
    brand_voice_summary: str | None,
    key_themes: list | None,
    primary_colors: list | None,
    content_dos: list | None,
    content_donts: list | None,
    suggested_post_ideas: list | None = None,
    tagline: str | None = None,
    visual_style: str | None = None,
    fonts: list | None = None,
    logo_url: str | None = None,
    core_products: list | None = None,
    website_signals: dict | None = None,
    industry_category_detected: str | None = None,
) -> dict:
    """Upsert - see agents/website_analysis_agent.py for how these fields are
    derived. One row per user (unique on user_id). website_signals holds the
    facts read off the site (see _WEBSITE_SIGNAL_FIELDS) - omitted for a
    manual-description analysis, which then leaves them empty."""
    with Session(engine) as session:
        row = session.query(UserBrandProfile).filter(UserBrandProfile.user_id == user_id).first()
        if row is None:
            row = UserBrandProfile(user_id=user_id, website=website)
            session.add(row)

        row.website = website
        row.company_name = company_name
        row.industry = industry
        row.target_audience = target_audience
        row.brand_voice_summary = brand_voice_summary
        row.key_themes = json.dumps(key_themes or [])
        row.primary_colors = json.dumps(primary_colors or [])
        row.content_dos = json.dumps(content_dos or [])
        row.content_donts = json.dumps(content_donts or [])
        row.core_products = json.dumps(core_products or [])
        row.suggested_post_ideas = json.dumps(suggested_post_ideas or [])
        row.tagline = tagline
        row.visual_style = visual_style
        row.fonts = json.dumps(fonts or [])
        row.logo_url = logo_url
        signals = website_signals or {}
        for field, empty in _WEBSITE_SIGNAL_FIELDS.items():
            setattr(row, field, json.dumps(signals.get(field) or empty))
        row.industry_category_detected = industry_category_detected
        if row.compliance_confirmed_at is None:
            # Not confirmed by the user yet - follow the latest detection
            row.industry_category = industry_category_detected
            row.compliance_regions = json.dumps(signals.get("regions_detected") or [])
        row.analyzed_at = _utcnow()
        session.commit()
        session.refresh(row)
        return {"id": row.id, "user_id": row.user_id, "website": row.website}


# UserBrandProfile JSON columns holding facts read off the website -> empty value
_WEBSITE_SIGNAL_FIELDS = {
    "schema_types": [],
    "social_links": {},
    "legal_pages": [],
    "region_signals": {},
    "regions_detected": [],
    "site_disclaimers": [],
    "certifications": [],
}


_BRAND_PROFILE_TEXT_FIELDS = {
    "company_name",
    "website",
    "industry",
    "target_audience",
    "brand_voice_summary",
    "tagline",
    "visual_style",
    "logo_url",
    "country",
}
_BRAND_PROFILE_LIST_FIELDS = {"key_themes", "primary_colors", "content_dos", "content_donts", "fonts", "core_products"}


def update_user_brand_profile_fields(user_id: int, **fields) -> dict | None:
    """Partial update for the "My Brand Configuration" self-edit page (see
    api/routes.py's PUT /api/brand-profile) - unlike save_user_brand_profile
    (positional, used by the scrape pipeline), only touches columns
    explicitly passed in. Returns None if the user has no profile yet (the
    edit page requires onboarding's scrape to have created one first)."""
    with Session(engine) as session:
        row = session.query(UserBrandProfile).filter(UserBrandProfile.user_id == user_id).first()
        if row is None:
            return None

        for key, value in fields.items():
            if key in _BRAND_PROFILE_TEXT_FIELDS:
                setattr(row, key, value)
            elif key in _BRAND_PROFILE_LIST_FIELDS:
                setattr(row, key, json.dumps(value or []))

        row.analyzed_at = _utcnow()
        session.commit()
        session.refresh(row)
        return {"id": row.id, "user_id": row.user_id}


def set_approval_reviewer_email(user_id: int, email: str | None) -> None:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is not None:
            user.approval_reviewer_email = (email or "").strip().lower() or None
            session.commit()


def get_industry_trends(key: str) -> dict | None:
    """{"topics": [...], "source": ..., "fetched_at": aware datetime} or None."""
    with Session(engine) as session:
        row = session.get(IndustryTrend, key)
        if not row:
            return None
        return {"topics": json.loads(row.payload or "[]"), "source": row.source, "fetched_at": _as_utc(row.fetched_at)}


def save_industry_trends(key: str, topics: list[dict], source: str) -> None:
    with Session(engine) as session:
        row = session.get(IndustryTrend, key)
        if row is None:
            row = IndustryTrend(key=key)
            session.add(row)
        row.payload = json.dumps(topics)
        row.source = source
        row.fetched_at = _utcnow()
        session.commit()


def list_industry_trend_keys() -> list[tuple[str, datetime]]:
    """(key, fetched_at) of every cached industry - for the scheduler's daily refresh."""
    with Session(engine) as session:
        return [(r.key, _as_utc(r.fetched_at)) for r in session.query(IndustryTrend).all()]


def get_user_idea_feed(user_id: int) -> dict | None:
    """{"feed": {...}, "generated_at": aware datetime} or None."""
    with Session(engine) as session:
        row = session.get(UserIdeaFeed, user_id)
        if not row:
            return None
        return {"feed": json.loads(row.payload or "{}"), "generated_at": _as_utc(row.generated_at)}


def save_user_idea_feed(user_id: int, feed: dict) -> None:
    with Session(engine) as session:
        row = session.get(UserIdeaFeed, user_id)
        if row is None:
            row = UserIdeaFeed(user_id=user_id)
            session.add(row)
        row.payload = json.dumps(feed)
        row.generated_at = _utcnow()
        session.commit()


def create_idea_link(user_id: int, idea: dict) -> str:
    token = secrets.token_urlsafe(24)
    with Session(engine) as session:
        session.add(IdeaLink(token=token, user_id=user_id, payload=json.dumps(idea)))
        session.commit()
    return token


def open_idea_link(token: str, user_id: int) -> dict | None:
    """The idea behind an emailed link - only for the user it was sent to."""
    with Session(engine) as session:
        row = session.get(IdeaLink, token)
        if not row or row.user_id != user_id:
            return None
        if row.opened_at is None:
            row.opened_at = _utcnow()
            session.commit()
        return json.loads(row.payload or "{}")


# ── Content Calendar: weekly goal + what was created / scheduled ──────────

WEEKLY_POST_GOAL_DEFAULT = 3


def get_weekly_post_goal(user_id: int) -> int:
    with Session(engine) as session:
        user = session.get(User, user_id)
        return int(user.weekly_post_goal) if user and user.weekly_post_goal else WEEKLY_POST_GOAL_DEFAULT


def set_weekly_post_goal(user_id: int, goal: int) -> bool:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            return False
        user.weekly_post_goal = max(1, min(7, int(goal)))
        session.commit()
        return True


def posts_created_between(user_id: int, start: datetime, end: datetime) -> list[dict]:
    """The posts the user created in [start, end): one per brief. Refinements
    and regenerated versions of a post are the same post, so they don't count."""
    lo = start.astimezone(timezone.utc).replace(tzinfo=None)
    hi = end.astimezone(timezone.utc).replace(tzinfo=None)
    with Session(engine) as session:
        rows = (
            session.query(RunHistory)
            .filter(RunHistory.user_id == user_id, RunHistory.created_at >= lo, RunHistory.created_at < hi)
            .order_by(RunHistory.created_at.asc())
            .all()
        )
        posts = []
        for row in rows:
            try:
                content = json.loads(row.content or "{}")
                platforms = json.loads(row.platforms or "[]")
            except (TypeError, ValueError):
                content, platforms = {}, []
            meta = content.get("_meta") if isinstance(content, dict) else None
            if isinstance(meta, dict) and (meta.get("refined_from_run_id") or meta.get("version_of")):
                continue
            media = [(v.get("media") or {}) for k, v in content.items() if isinstance(v, dict) and not str(k).startswith("_")]
            posts.append({
                "id": row.id,
                "created_at": _as_utc(row.created_at),
                "title": " ".join((row.story or "").split())[:90],
                "platforms": platforms if isinstance(platforms, list) else [],
                "conversation_id": row.conversation_id,
                "has_image": any(m.get("image") for m in media),
                "has_video": any(m.get("video") for m in media),
            })
        return posts


def scheduled_posts_between(user_id: int, start: datetime, end: datetime) -> list[dict]:
    """The calendar's scheduled posts (not the cancelled ones), with scheduled_at as an aware UTC datetime."""
    lo = start.astimezone(timezone.utc).replace(tzinfo=None)
    hi = end.astimezone(timezone.utc).replace(tzinfo=None)
    with Session(engine) as session:
        rows = (
            session.query(ScheduledPost)
            .filter(ScheduledPost.user_id == user_id, ScheduledPost.scheduled_at >= lo, ScheduledPost.scheduled_at < hi,
                    ScheduledPost.status != "cancelled")
            .order_by(ScheduledPost.scheduled_at.asc())
            .all()
        )
        out = []
        for row in rows:
            post = _scheduled_post_dict(row)
            out.append({**{k: post[k] for k in ("id", "status", "platforms", "title", "thumb", "results", "media", "conversation_id")},
                        "scheduled_at": _as_utc(row.scheduled_at), "movable": row.status == "pending"})
        return out


# ── Header bell (Notification) ────────────────────────────────────────────


def _serialize_notification(row: "Notification") -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "url": row.url,
        "created_at": _as_utc(row.created_at).isoformat(),
        "read": row.read_at is not None,
    }


def has_notification(user_id: int, dedupe_key: str) -> bool:
    with Session(engine) as session:
        return session.query(Notification.id).filter(
            Notification.user_id == user_id, Notification.dedupe_key == dedupe_key
        ).first() is not None


def create_notification(
    user_id: int, kind: str, title: str, body: str, url: str, dedupe_key: str, when: datetime | None = None
) -> int | None:
    """The new notification's id, or None when this nudge already exists for the user."""
    with Session(engine) as session:
        if session.query(Notification.id).filter(
            Notification.user_id == user_id, Notification.dedupe_key == dedupe_key
        ).first():
            return None
        row = Notification(user_id=user_id, kind=kind, title=title[:200], body=body, url=url, dedupe_key=dedupe_key[:160],
                           created_at=(when or _utcnow()).astimezone(timezone.utc).replace(tzinfo=None))
        session.add(row)
        session.commit()
        return row.id


def list_notifications(user_id: int, limit: int = 15) -> dict:
    """{"unread": n, "items": newest first} for the header bell."""
    with Session(engine) as session:
        base = session.query(Notification).filter(Notification.user_id == user_id)
        unread = base.filter(Notification.read_at.is_(None)).count()
        rows = base.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit).all()
        return {"unread": unread, "items": [_serialize_notification(r) for r in rows]}


def mark_notifications_read(user_id: int, ids: list[int] | None = None) -> int:
    """Mark the given notifications (or, with ids=None, all of them) read. Returns how many changed."""
    with Session(engine) as session:
        query = session.query(Notification).filter(Notification.user_id == user_id, Notification.read_at.is_(None))
        if ids is not None:
            query = query.filter(Notification.id.in_([int(i) for i in ids] or [0]))
        changed = query.update({"read_at": _utcnow()}, synchronize_session=False)
        session.commit()
        return int(changed)


def mark_notification_emailed(notification_id: int, when: datetime | None = None) -> None:
    with Session(engine) as session:
        session.query(Notification).filter(Notification.id == notification_id).update(
            {"emailed_at": (when or _utcnow()).astimezone(timezone.utc).replace(tzinfo=None)})
        session.commit()


def last_notification_at(user_id: int, kind: str | None = None) -> datetime | None:
    with Session(engine) as session:
        query = session.query(func.max(Notification.created_at)).filter(Notification.user_id == user_id)
        if kind:
            query = query.filter(Notification.kind == kind)
        return _as_utc(query.scalar())


def emails_sent_since(user_id: int, since: datetime) -> int:
    """Idea emails (the weekly one + emailed nudges) sent to the user since `since` - for the weekly cap."""
    cutoff = since.astimezone(timezone.utc).replace(tzinfo=None)
    with Session(engine) as session:
        user = session.get(User, user_id)
        weekly = 1 if user and user.ideas_email_last_sent_at and user.ideas_email_last_sent_at >= cutoff else 0
        nudges = session.query(func.count(Notification.id)).filter(
            Notification.user_id == user_id, Notification.emailed_at >= cutoff
        ).scalar() or 0
        return weekly + int(nudges)


def last_activity_at(user_id: int) -> datetime | None:
    """When the user last generated something."""
    with Session(engine) as session:
        return _as_utc(session.query(func.max(RunHistory.created_at)).filter(RunHistory.user_id == user_id).scalar())


def users_for_nudges() -> list[dict]:
    """Active users with a brand profile - the ones nudges can be written for."""
    with Session(engine) as session:
        rows = (
            session.query(User)
            .join(UserBrandProfile, UserBrandProfile.user_id == User.id)
            .filter(User.is_active.isnot(False))
            .all()
        )
        return [
            {"id": u.id, "name": u.name, "email": u.email, "created_at": _as_utc(u.created_at),
             "email_ok": bool(u.email_verified) and u.nudge_email_enabled is not False}
            for u in rows
        ]


def set_recap_email_enabled(user_id: int, enabled: bool) -> bool:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            return False
        user.recap_email_enabled = bool(enabled)
        session.commit()
        return True


def mark_recap_email_sent(user_id: int, month_key: str) -> None:
    with Session(engine) as session:
        session.query(User).filter(User.id == user_id).update({"recap_email_last_month": month_key})
        session.commit()


def users_due_recap(month_key: str) -> list[dict]:
    """Active, verified users who haven't switched the monthly recap off and
    haven't been sent the one for `month_key` ("2026-09") yet."""
    with Session(engine) as session:
        rows = (
            session.query(User)
            .filter(
                User.is_active.isnot(False),
                User.email_verified.is_(True),
                User.recap_email_enabled.isnot(False),
                (User.recap_email_last_month.is_(None)) | (User.recap_email_last_month != month_key),
            )
            .all()
        )
        return [{"id": u.id, "name": u.name, "email": u.email} for u in rows]


def set_nudge_email_enabled(user_id: int, enabled: bool) -> bool:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            return False
        user.nudge_email_enabled = bool(enabled)
        session.commit()
        return True


def get_ideas_email_settings(user_id: int) -> dict | None:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            return None
        return {
            "enabled": user.ideas_email_enabled is not False,
            "nudges_enabled": user.nudge_email_enabled is not False,
            "recap_enabled": user.recap_email_enabled is not False,
            "recap_last_month": user.recap_email_last_month,
            "email": user.email,
            "last_sent_at": _as_utc(user.ideas_email_last_sent_at).isoformat() if user.ideas_email_last_sent_at else None,
        }


def set_ideas_email_enabled(user_id: int, enabled: bool) -> bool:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            return False
        user.ideas_email_enabled = bool(enabled)
        session.commit()
        return True


def mark_ideas_email_sent(user_id: int) -> None:
    with Session(engine) as session:
        session.query(User).filter(User.id == user_id).update({"ideas_email_last_sent_at": _utcnow()})
        session.commit()


def users_due_ideas_email(min_days_between: int = 6, quiet_days_after_activity: int = 2) -> list[dict]:
    """Who gets this week's ideas email: active, verified users with a brand
    profile who haven't switched it off, weren't sent one in the last
    `min_days_between` days, and haven't created anything in the last
    `quiet_days_after_activity` days (they're already here - no nudge needed)."""
    now = _utcnow()
    sent_cutoff = (now - timedelta(days=min_days_between)).replace(tzinfo=None)
    active_cutoff = (now - timedelta(days=quiet_days_after_activity)).replace(tzinfo=None)
    with Session(engine) as session:
        recently_active = {
            uid for (uid,) in session.query(RunHistory.user_id).filter(RunHistory.created_at >= active_cutoff).distinct()
        }
        rows = (
            session.query(User)
            .join(UserBrandProfile, UserBrandProfile.user_id == User.id)
            .filter(
                User.is_active.isnot(False),
                User.email_verified.is_(True),
                User.ideas_email_enabled.isnot(False),
                (User.ideas_email_last_sent_at.is_(None)) | (User.ideas_email_last_sent_at < sent_cutoff),
            )
            .all()
        )
        return [{"id": u.id, "name": u.name, "email": u.email} for u in rows if u.id not in recently_active]


def set_brand_post_ideas(user_id: int, ideas: list[dict]) -> None:
    """Replaces the "Start from an idea" pool (newest first) - see
    brand_profile_service.generate_post_ideas."""
    with Session(engine) as session:
        row = session.query(UserBrandProfile).filter(UserBrandProfile.user_id == user_id).first()
        if row is not None:
            row.suggested_post_ideas = json.dumps(ideas)
            session.commit()


def update_compliance_profile(
    user_id: int, industry_category: str, regions: list[str], excluded_rule_ids: list[str]
) -> bool:
    """The user's confirmed compliance settings (see services/compliance_rules.py).
    Returns False if the user has no brand profile yet."""
    with Session(engine) as session:
        row = session.query(UserBrandProfile).filter(UserBrandProfile.user_id == user_id).first()
        if row is None:
            return False
        row.industry_category = industry_category
        row.compliance_regions = json.dumps(regions)
        row.compliance_excluded_rules = json.dumps(excluded_rule_ids)
        row.compliance_confirmed_at = _utcnow()
        session.commit()
        return True


def set_brand_scan_status(user_id: int, status: str, error: str | None = None) -> None:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None:
            return
        user.brand_scan_status = status
        user.brand_scan_error = error
        user.brand_scan_updated_at = _utcnow()
        session.commit()


def get_brand_scan_status(user_id: int) -> dict:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None:
            return {"status": None, "error": None, "updated_at": None}
        return {
            "status": user.brand_scan_status,
            "error": user.brand_scan_error,
            "updated_at": user.brand_scan_updated_at.strftime("%Y-%m-%d %H:%M:%S") if user.brand_scan_updated_at else None,
        }


def get_user_brand_profile(user_id: int) -> dict | None:
    with Session(engine) as session:
        row = session.query(UserBrandProfile).filter(UserBrandProfile.user_id == user_id).first()
        if not row:
            return None
        return {
            "website": row.website,
            "company_name": row.company_name,
            "industry": row.industry,
            "target_audience": row.target_audience,
            "brand_voice_summary": row.brand_voice_summary,
            "key_themes": json.loads(row.key_themes) if row.key_themes else [],
            "primary_colors": json.loads(row.primary_colors) if row.primary_colors else [],
            "content_dos": json.loads(row.content_dos) if row.content_dos else [],
            "content_donts": json.loads(row.content_donts) if row.content_donts else [],
            "core_products": json.loads(row.core_products) if getattr(row, 'core_products', None) else [],
            "suggested_post_ideas": json.loads(row.suggested_post_ideas) if row.suggested_post_ideas else [],
            "tagline": row.tagline,
            "visual_style": row.visual_style,
            "fonts": json.loads(row.fonts) if row.fonts else [],
            "logo_url": row.logo_url,
            "country": getattr(row, "country", None),
            **{
                field: json.loads(getattr(row, field)) if getattr(row, field, None) else empty
                for field, empty in _WEBSITE_SIGNAL_FIELDS.items()
            },
            "industry_category": row.industry_category,
            "industry_category_detected": row.industry_category_detected,
            "compliance_regions": json.loads(row.compliance_regions) if row.compliance_regions else [],
            "compliance_excluded_rules": json.loads(row.compliance_excluded_rules) if row.compliance_excluded_rules else [],
            "compliance_confirmed_at": (
                row.compliance_confirmed_at.strftime("%Y-%m-%d %H:%M:%S") if row.compliance_confirmed_at else None
            ),
            "analyzed_at": row.analyzed_at.strftime("%Y-%m-%d %H:%M:%S"),
        }


def save_run(
    story: str,
    tone: str,
    platforms: list,
    content: dict,
    user_id: int,
    tokens_used: int = 0,
    cost_usd: float = 0.0,
    conversation_id: int | None = None,
) -> int:
    with Session(engine) as session:
        row = RunHistory(
            user_id=user_id,
            conversation_id=conversation_id,
            story=story,
            tone=tone or "Auto",
            platforms=json.dumps(platforms),
            content=json.dumps(content),
            tokens_used=tokens_used,
            cost_usd=cost_usd,
            is_archived=False,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return int(row.id)  # type: ignore


def save_approved_asset(user_id: int, platform: str, content_type: str, content_data: str) -> int:
    with Session(engine) as session:
        row = ApprovedAsset(user_id=user_id, platform=platform, content_type=content_type, content_data=content_data)
        session.add(row)
        session.commit()
        session.refresh(row)
        return int(row.id)  # type: ignore


def update_run_content(run_id: int, content: dict, user_id: int | None = None) -> bool:
    with Session(engine) as session:
        row = session.get(RunHistory, run_id)
        if not row:
            return False
        if user_id is not None and row.user_id != user_id:
            return False
        row.content = json.dumps(content)  # type: ignore
        session.commit()
        return True


# ── Conversations (Studio Chat threads) ───────────────────────────────────
# Every message of a thread is saved as its own run (cost, media and admin
# history stay per run); the conversation groups them. Only "New
# Conversation" starts a new one - the client sends conversation_id with each
# message, and a regenerated reply records content._meta.version_of.


def ensure_conversation(user_id: int, conversation_id: int | None, title: str) -> int:
    """The user's conversation to save the next message into: the given one
    when it is theirs (bumped to the top, restored if archived), else a new one.
    Another user's id is never reused - that starts a new conversation."""
    now = _utcnow()
    with Session(engine) as session:
        conv = None
        if conversation_id:
            try:
                conv = session.get(Conversation, int(conversation_id))
            except (TypeError, ValueError):
                conv = None
        if conv is None or conv.user_id != user_id:
            conv = Conversation(user_id=user_id, title=(title or "New conversation").strip()[:300], created_at=now)
            session.add(conv)
        conv.updated_at = now
        conv.is_archived = False
        session.commit()
        return int(conv.id)


def _conversation_summary(conv: "Conversation", runs: list) -> dict:
    platforms: list[str] = []
    for r in runs:
        for p in json.loads(r.platforms) if r.platforms else []:
            if p not in platforms:
                platforms.append(p)
    latest = runs[-1] if runs else None
    title = conv.title or ""
    short = title[:120] + ("..." if len(title) > 120 else "")
    return {
        "id": conv.id,
        "title": short,
        "story": short,  # the sidebar list and its search read "story"
        "platforms": platforms,
        "tone": latest.tone if latest else None,
        "message_count": len(runs),
        "timestamp": (conv.updated_at or conv.created_at).strftime("%Y-%m-%d %H:%M"),
        # UTC, for the sidebar's "3d" / "Oct 9" and its date groups
        "updated_at": (conv.updated_at or conv.created_at).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "is_archived": bool(conv.is_archived),
        "is_pinned": conv.pinned_at is not None,
        "active_image_id": conv.active_image_id,
    }


def list_conversations(user_id: int, limit: int = 30, archived: bool = False) -> list[dict]:
    with Session(engine) as session:
        convs = (
            session.query(Conversation)
            .filter(Conversation.user_id == user_id, Conversation.is_archived.is_(bool(archived)))
            # Pinned first (newest pin on top), so they are never cut off by the limit
            .order_by(Conversation.pinned_at.is_(None), Conversation.pinned_at.desc(),
                      Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(limit)
            .all()
        )
        if not convs:
            return []
        runs_by_conv: dict[int, list] = {c.id: [] for c in convs}
        for r in (
            session.query(RunHistory)
            .filter(RunHistory.conversation_id.in_(list(runs_by_conv)), RunHistory.user_id == user_id)
            .order_by(RunHistory.created_at.asc(), RunHistory.id.asc())
            .all()
        ):
            runs_by_conv[r.conversation_id].append(r)
        return [_conversation_summary(c, runs_by_conv[c.id]) for c in convs if runs_by_conv[c.id]]


def get_conversation(conversation_id: int, user_id: int) -> dict | None:
    """The conversation and all its runs, oldest first (to replay the thread)."""
    with Session(engine) as session:
        conv = session.get(Conversation, conversation_id)
        if not conv or conv.user_id != user_id:
            return None
        runs = (
            session.query(RunHistory)
            .filter(RunHistory.conversation_id == conversation_id, RunHistory.user_id == user_id)
            .order_by(RunHistory.created_at.asc(), RunHistory.id.asc())
            .all()
        )
        return {"conversation": _conversation_summary(conv, runs), "runs": [_serialize_run(r) for r in runs]}


def set_conversation_archived(conversation_id: int, user_id: int, archived: bool) -> bool:
    with Session(engine) as session:
        conv = session.get(Conversation, conversation_id)
        if not conv or conv.user_id != user_id:
            return False
        conv.is_archived = bool(archived)
        if archived:
            conv.pinned_at = None  # an archived chat leaves the Pinned list
        session.commit()
        return True


def set_conversation_pinned(conversation_id: int, user_id: int, pinned: bool) -> bool:
    """Pins a conversation to the top of the sidebar, or unpins it."""
    with Session(engine) as session:
        conv = session.get(Conversation, conversation_id)
        if not conv or conv.user_id != user_id:
            return False
        conv.pinned_at = _utcnow() if pinned else None
        session.commit()
        return True


# ── Image versions (lineage) ──────────────────────────────────────────────
# Each generated/edited image is an ImageGeneration row: parent_id = the image
# it was edited from, root_id = the original of that lineage, version = depth
# (1 = original). Editing an older version branches (a second child of it);
# nothing is overwritten. The conversation's active_image_id is the image
# "it" / "this" refers to.


def _asset_dict(r: "ImageGeneration") -> dict:
    return {
        "id": r.id,
        "conversation_id": r.conversation_id,
        "run_id": r.run_id,
        "parent_id": r.parent_id,
        "root_id": r.root_id,
        "version": r.version or 1,
        "kind": r.kind,
        "platform": r.platform,
        "model": r.model,
        "prompt": r.prompt or r.description,
        "edit_instruction": r.edit_instruction,
        "url": r.media_url,
        "clean_url": r.clean_url,
        "width": r.width,
        "height": r.height,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _library_chains(session, user_id: int):
    """One row per image in the library: the newest version of each edit chain
    (an original and its edits share root_id), with how many versions it has.
    -> subquery (root, latest, versions)"""
    root = func.coalesce(ImageGeneration.root_id, ImageGeneration.id)
    return (
        session.query(root.label("root"), func.max(ImageGeneration.id).label("latest"),
                      func.count(ImageGeneration.id).label("versions"))
        .filter(ImageGeneration.user_id == user_id, ImageGeneration.kind.in_(("image", "edit")),
                ImageGeneration.media_url.isnot(None), ImageGeneration.media_url != "",
                ImageGeneration.deleted_at.is_(None))
        .group_by(root)
        .subquery()
    )


def _library_search(rows, query: str | None):
    """Every word must appear in what the image was asked to show."""
    for word in [w for w in (query or "").lower().split() if w][:6]:
        # % and _ typed by the user are searched for as themselves
        like = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = rows.filter(or_(func.lower(func.coalesce(ImageGeneration.prompt, "")).like(like, escape="\\"),
                               func.lower(func.coalesce(ImageGeneration.description, "")).like(like, escape="\\"),
                               func.lower(func.coalesce(ImageGeneration.edit_instruction, "")).like(like, escape="\\")))
    return rows


def list_user_images(user_id: int, limit: int = 60, offset: int = 0, query: str | None = None,
                     conversation: int | str | None = None, favorites: bool = False) -> tuple[list[dict], int]:
    """The image library, newest first: one entry per image (its newest
    version; "versions" says how many there are), optionally only those whose
    description matches, from one conversation ("none" = images made outside
    one) or starred. -> (images, total)"""
    with Session(engine) as session:
        chains = _library_chains(session, user_id)
        rows = (session.query(ImageGeneration, chains.c.versions, chains.c.root)
                .join(chains, ImageGeneration.id == chains.c.latest))
        rows = _library_search(rows, query)
        if conversation == "none":
            rows = rows.filter(ImageGeneration.conversation_id.is_(None))
        elif conversation is not None:
            rows = rows.filter(ImageGeneration.conversation_id == int(conversation))
        starred = {r for (r,) in session.query(ImageGeneration.id).filter(
            ImageGeneration.user_id == user_id, ImageGeneration.favorite_at.isnot(None))}
        if favorites:
            rows = rows.filter(chains.c.root.in_(starred or {0}))
        total = rows.count()
        page = rows.order_by(ImageGeneration.id.desc()).offset(max(0, offset)).limit(max(1, min(limit, 200))).all()
        return [{**_asset_dict(r), "versions": int(n), "root_id": int(root), "favorite": int(root) in starred}
                for r, n, root in page], total


def list_library_folders(user_id: int, query: str | None = None) -> list[dict]:
    """The library by campaign: one folder per Studio Chat conversation that
    made images, plus one for images made outside a conversation; newest
    first. A search keeps the folders whose title or any image matches.
    -> [{conversation_id, title, count, covers: [url x4], updated_at}]"""
    with Session(engine) as session:
        chains = _library_chains(session, user_id)
        rows = (session.query(ImageGeneration.conversation_id, ImageGeneration.media_url, ImageGeneration.created_at,
                              ImageGeneration.id)
                .join(chains, ImageGeneration.id == chains.c.latest)
                .order_by(ImageGeneration.id.desc()).all())
        matching = None
        if query:
            matching = {c for (c,) in _library_search(
                session.query(ImageGeneration.conversation_id).join(chains, ImageGeneration.id == chains.c.latest), query)}
        titles = dict(session.query(Conversation.id, Conversation.title).filter(Conversation.user_id == user_id))
        folders: dict = {}
        for conv_id, url, created, _ in rows:
            folder = folders.setdefault(conv_id, {"conversation_id": conv_id, "count": 0, "covers": [],
                                                  "updated_at": _as_utc(created).isoformat() if created else None})
            folder["count"] += 1
            if len(folder["covers"]) < 4:
                folder["covers"].append(url)
        words = [w for w in (query or "").lower().split() if w]
        out = []
        for folder in folders.values():
            folder["title"] = titles.get(folder["conversation_id"]) or (
                "Other images" if folder["conversation_id"] is None else "Untitled campaign")
            if query and folder["conversation_id"] not in matching and not all(
                    w in folder["title"].lower() for w in words):
                continue
            out.append(folder)
        return out


def list_image_versions(user_id: int, image_id: int) -> list[dict]:
    """Every version of an image (its original and edits), oldest first."""
    with Session(engine) as session:
        row = session.get(ImageGeneration, image_id)
        if row is None or row.user_id != user_id:
            return []
        root = row.root_id or row.id
        chain = (session.query(ImageGeneration)
                 .filter(ImageGeneration.user_id == user_id, or_(ImageGeneration.id == root, ImageGeneration.root_id == root),
                         ImageGeneration.media_url.isnot(None), ImageGeneration.media_url != "",
                         ImageGeneration.deleted_at.is_(None))
                 .order_by(ImageGeneration.id).all())
        return [_asset_dict(r) for r in chain]


def set_image_favorite(user_id: int, image_id: int, on: bool) -> bool | None:
    """Star or unstar an image (its whole edit chain). None when it isn't the user's."""
    with Session(engine) as session:
        row = session.get(ImageGeneration, image_id)
        if row is None or row.user_id != user_id:
            return None
        root = session.get(ImageGeneration, row.root_id or row.id) or row
        root.favorite_at = _utcnow() if on else None
        session.commit()
        return on


def delete_library_image(user_id: int, image_id: int) -> int:
    """Remove an image and all its versions from the library. The files stay,
    so posts and schedules that use them keep working. -> versions removed"""
    with Session(engine) as session:
        row = session.get(ImageGeneration, image_id)
        if row is None or row.user_id != user_id:
            return 0
        root = row.root_id or row.id
        removed = (session.query(ImageGeneration)
                   .filter(ImageGeneration.user_id == user_id, ImageGeneration.deleted_at.is_(None),
                           or_(ImageGeneration.id == root, ImageGeneration.root_id == root))
                   .update({"deleted_at": _utcnow()}, synchronize_session=False))
        session.commit()
        return removed


# ── Saved briefs (Studio Chat: the star on a message) ──────────────────────
MAX_SAVED_BRIEFS = 50


def _saved_brief_dict(row: "SavedBrief") -> dict:
    try:
        platforms = json.loads(row.platforms) if row.platforms else []
    except (TypeError, ValueError):
        platforms = []
    return {"id": row.id, "text": row.text, "platforms": platforms if isinstance(platforms, list) else [],
            "use_count": row.use_count or 0, "created_at": _as_utc(row.created_at).isoformat()}


def list_saved_briefs(user_id: int) -> list[dict]:
    """Most recently used (or saved) first."""
    with Session(engine) as session:
        rows = session.query(SavedBrief).filter(SavedBrief.user_id == user_id).all()
        rows.sort(key=lambda r: r.last_used_at or r.created_at, reverse=True)
        return [_saved_brief_dict(r) for r in rows]


def save_brief(user_id: int, text: str, platforms: list | None = None) -> tuple[dict | None, bool]:
    """(the saved brief, True when it is new). The same text is saved once.
    (None, False) when the user already has MAX_SAVED_BRIEFS."""
    text = (text or "").strip()[:4000]
    with Session(engine) as session:
        rows = session.query(SavedBrief).filter(SavedBrief.user_id == user_id).all()
        same = next((r for r in rows if r.text.strip().lower() == text.lower()), None)
        if same:
            return _saved_brief_dict(same), False
        if len(rows) >= MAX_SAVED_BRIEFS:
            return None, False
        row = SavedBrief(user_id=user_id, text=text, platforms=json.dumps([str(p) for p in (platforms or [])][:6]))
        session.add(row)
        session.commit()
        session.refresh(row)
        return _saved_brief_dict(row), True


def delete_saved_brief(user_id: int, brief_id: int) -> bool:
    with Session(engine) as session:
        row = session.query(SavedBrief).filter(SavedBrief.id == brief_id, SavedBrief.user_id == user_id).first()
        if not row:
            return False
        session.delete(row)
        session.commit()
        return True


def mark_brief_used(user_id: int, brief_id: int) -> bool:
    with Session(engine) as session:
        row = session.query(SavedBrief).filter(SavedBrief.id == brief_id, SavedBrief.user_id == user_id).first()
        if not row:
            return False
        row.use_count = (row.use_count or 0) + 1
        row.last_used_at = _utcnow().replace(tzinfo=None)
        session.commit()
        return True


def recent_hiring_designs(user_id: int, limit: int = 5) -> list[dict]:
    """The designs of the user's last /hiring posts, newest first (services/hiring_service.py)."""
    with Session(engine) as session:
        rows = (
            session.query(RunHistory.content)
            .filter(RunHistory.user_id == user_id, RunHistory.story.like("/hiring%"))
            .order_by(RunHistory.id.desc())
            .limit(limit * 2)
            .all()
        )
    designs = []
    for (content,) in rows:
        try:
            design = (((json.loads(content or "{}") or {}).get("_preset") or {}).get("hiring") or {}).get("design")
        except (TypeError, ValueError, AttributeError):
            design = None
        if isinstance(design, dict):
            designs.append(design)
    return designs[:limit]


def get_image_asset(asset_id: int | None, user_id: int) -> dict | None:
    if not asset_id:
        return None
    with Session(engine) as session:
        try:
            row = session.get(ImageGeneration, int(asset_id))
        except (TypeError, ValueError):
            return None
        if not row or row.user_id != user_id or row.kind not in ("image", "edit"):
            return None
        return _asset_dict(row)


def image_lineage(asset_id: int, user_id: int, max_depth: int = 25) -> list[dict]:
    """The image and its ancestors, original first."""
    chain: list[dict] = []
    with Session(engine) as session:
        row = session.get(ImageGeneration, asset_id)
        while row is not None and row.user_id == user_id and len(chain) < max_depth:
            chain.append(_asset_dict(row))
            row = session.get(ImageGeneration, row.parent_id) if row.parent_id else None
    return list(reversed(chain))


def conversation_images(conversation_id: int | None, user_id: int) -> list[dict]:
    """Every image version in the user's conversation, oldest first."""
    if not conversation_id:
        return []
    with Session(engine) as session:
        conv = session.get(Conversation, conversation_id)
        if not conv or conv.user_id != user_id:
            return []
        rows = (
            session.query(ImageGeneration)
            .filter(
                ImageGeneration.conversation_id == conversation_id,
                ImageGeneration.user_id == user_id,
                ImageGeneration.kind.in_(("image", "edit")),
            )
            .order_by(ImageGeneration.id.asc())
            .all()
        )
        return [_asset_dict(r) for r in rows]


def get_active_image_id(conversation_id: int | None, user_id: int) -> int | None:
    if not conversation_id:
        return None
    with Session(engine) as session:
        conv = session.get(Conversation, conversation_id)
        return conv.active_image_id if conv and conv.user_id == user_id else None


def set_active_image(conversation_id: int, user_id: int, asset_id: int) -> bool:
    """The user picked a version ("Refine this version"): "it" now means that image."""
    with Session(engine) as session:
        conv = session.get(Conversation, conversation_id)
        asset = session.get(ImageGeneration, asset_id)
        if not conv or conv.user_id != user_id or not asset or asset.user_id != user_id:
            return False
        if asset.conversation_id not in (None, conversation_id):
            return False
        conv.active_image_id = asset.id
        session.commit()
        return True


def attach_images_to_run(asset_ids: list[int], run_id: int, user_id: int) -> None:
    """Edits are logged before their run is saved (so the run's content can
    reference them); link them to the run once it exists. Until then a row has
    no run and counts as its own charge - so if saving the run fails, the image
    the user received is still charged once."""
    if not asset_ids or not run_id:
        return
    with Session(engine) as session:
        run = session.get(RunHistory, run_id)
        if not run or run.user_id != user_id:
            return
        session.query(ImageGeneration).filter(
            ImageGeneration.id.in_(list(asset_ids)), ImageGeneration.user_id == user_id
        ).update({ImageGeneration.run_id: run_id}, synchronize_session=False)
        session.commit()


def _backfill_conversations() -> int:
    """Runs saved before conversations existed: each becomes its own
    one-message conversation (which runs belonged together wasn't recorded).
    Their images join that conversation as originals of their own lineage."""
    made = 0
    with Session(engine) as session:
        while True:
            rows = (
                session.query(RunHistory)
                .filter(RunHistory.conversation_id.is_(None), RunHistory.user_id.isnot(None))
                .order_by(RunHistory.id.asc())
                .limit(500)
                .all()
            )
            if not rows:
                break
            for r in rows:
                conv = Conversation(
                    user_id=r.user_id,
                    title=(r.story or "Conversation")[:300],
                    created_at=r.created_at,
                    updated_at=r.created_at,
                    is_archived=bool(r.is_archived),
                )
                session.add(conv)
                session.flush()
                r.conversation_id = conv.id
                made += 1
            session.commit()
        for img, conv_id in (
            session.query(ImageGeneration, RunHistory.conversation_id)
            .join(RunHistory, RunHistory.id == ImageGeneration.run_id)
            .filter(ImageGeneration.conversation_id.is_(None), RunHistory.conversation_id.isnot(None))
            .all()
        ):
            img.conversation_id = conv_id
        session.query(ImageGeneration).filter(ImageGeneration.root_id.is_(None)).update(
            {ImageGeneration.root_id: ImageGeneration.id, ImageGeneration.version: 1}, synchronize_session=False
        )
        session.commit()
    return made


def archive_run(run_id: int, user_id: int | None = None) -> bool:
    with Session(engine) as session:
        row = session.get(RunHistory, run_id)
        if not row:
            return False
        if user_id is not None and row.user_id != user_id:
            return False
        row.is_archived = True  # type: ignore
        session.commit()
        return True


def unarchive_run(run_id: int, user_id: int | None = None) -> bool:
    with Session(engine) as session:
        row = session.get(RunHistory, run_id)
        if not row:
            return False
        if user_id is not None and row.user_id != user_id:
            return False
        row.is_archived = False  # type: ignore
        session.commit()
        return True


def append_run_media(run_id: int, platform: str, media_type: str, media: dict, user_id: int) -> bool:
    run = get_run_by_id(run_id, user_id=user_id)
    if not run:
        return False

    content = run["content"]
    platform_data = content.setdefault(platform, {})
    media_store = platform_data.setdefault("media", {})
    media_store[media_type] = media
    return update_run_content(run_id, content, user_id=user_id)


def get_system_usage_totals() -> dict:
    """All-time totals across every user's runs (Admin -> Global Cost History)."""
    with Session(engine) as session:
        row = session.query(
            func.count(RunHistory.id),
            func.coalesce(func.sum(RunHistory.cost_usd), 0.0),
            func.coalesce(func.sum(RunHistory.tokens_used), 0),
        ).first()
        media_cost = _media_charges_total(session)
    runs, cost, tokens = row if row else (0, 0.0, 0)
    return {
        "total_runs": int(runs or 0),
        "total_system_cost_usd": round(float(cost or 0.0) + media_cost, 6),
        "total_tokens": int(tokens or 0),
        "media_charges_usd": round(media_cost, 6),
    }


def _media_charges_total(session, user_id: int | None = None) -> float:
    """Cost of images generated outside a run (run images are in the run's cost)."""
    query = session.query(func.coalesce(func.sum(ImageGeneration.cost_usd), 0.0)).filter(ImageGeneration.run_id.is_(None))
    if user_id is not None:
        query = query.filter(ImageGeneration.user_id == user_id)
    return float(query.scalar() or 0.0)


def log_image_generation(
    user_id: int,
    cost_usd: float,
    run_id: int | None = None,
    kind: str = "image",
    platform: str | None = None,
    model: str | None = None,
    description: str | None = None,
    media_url: str | None = None,
    *,
    conversation_id: int | None = None,
    parent_id: int | None = None,
    prompt: str | None = None,
    edit_instruction: str | None = None,
    clean_url: str | None = None,
    width: int | None = None,
    height: int | None = None,
) -> int:
    """Records a generated/edited image and returns its id (the image's asset
    id): counts toward the daily limit and, when run_id is None, is the charge
    for it (see ImageGeneration). parent_id = the image it was edited from;
    root and version follow from it. A new image or edit becomes the
    conversation's active image."""
    with Session(engine) as session:
        if conversation_id is None and run_id:
            run = session.get(RunHistory, run_id)
            if run is not None and run.user_id == user_id:
                conversation_id = run.conversation_id
        parent = session.get(ImageGeneration, parent_id) if parent_id else None
        if parent is not None and parent.user_id != user_id:
            parent = None
        row = ImageGeneration(
            user_id=user_id, run_id=run_id, kind=kind, platform=platform, model=model,
            description=(description or "")[:500] or None, media_url=media_url,
            cost_usd=round(float(cost_usd or 0.0), 6),
            conversation_id=conversation_id,
            parent_id=parent.id if parent else None,
            version=(parent.version or 1) + 1 if parent else 1,
            prompt=prompt or description,
            edit_instruction=(edit_instruction or "")[:1000] or None,
            clean_url=clean_url, width=width, height=height,
        )
        session.add(row)
        session.flush()
        row.root_id = (parent.root_id or parent.id) if parent else row.id
        if conversation_id and kind in ("image", "edit"):
            conv = session.get(Conversation, conversation_id)
            if conv is not None and conv.user_id == user_id:
                conv.active_image_id = row.id
        _use_pack_if_over_allowance(session, user_id, "video" if kind == "video" else "image")
        session.commit()
        return int(row.id)


# ── Payments (services/billing_service.py) ────────────────────────────────


def create_payment(user_id: int, provider: str, order_id: str, item: str, description: str,
                   amount: int, tax: int, currency: str) -> None:
    with Session(engine) as session:
        session.add(Payment(user_id=user_id, provider=provider, order_id=order_id, item=item, description=description,
                            amount=amount, tax=tax, currency=currency))
        session.commit()


def get_payment(order_id: str) -> dict | None:
    with Session(engine) as session:
        row = session.query(Payment).filter(Payment.order_id == order_id).first()
        if row is None:
            return None
        return {c.name: getattr(row, c.name) for c in Payment.__table__.columns}


def claim_payment_for_fulfilment(order_id: str, payment_id: str | None) -> dict | None:
    """Mark the order paid and claim it for fulfilment - once. Returns the
    payment the first time, None if it was already fulfilled (or unknown)."""
    with Session(engine) as session:
        row = session.query(Payment).filter(Payment.order_id == order_id).with_for_update().first()
        if row is None or row.fulfilled_at is not None:
            return None
        now = _utcnow()
        row.status = "paid"
        row.payment_id = payment_id or row.payment_id
        row.paid_at = row.paid_at or now
        row.fulfilled_at = now
        session.commit()
        return {c.name: getattr(row, c.name) for c in Payment.__table__.columns}


def mark_payment_failed(order_id: str, payment_id: str | None) -> None:
    with Session(engine) as session:
        session.query(Payment).filter(Payment.order_id == order_id, Payment.fulfilled_at.is_(None)).update(
            {"status": "failed", "payment_id": payment_id})
        session.commit()


def record_payment_event(event_id: str) -> bool:
    """True the first time an event id is seen, False for a repeat."""
    from sqlalchemy.exc import IntegrityError

    with Session(engine) as session:
        session.add(PaymentEvent(event_id=event_id[:80]))
        try:
            session.commit()
            return True
        except IntegrityError:
            session.rollback()
            return False


def get_payment_by_provider_id(payment_id: str) -> dict | None:
    """The payment a provider's payment id belongs to (Stripe pi_...)."""
    with Session(engine) as session:
        row = session.query(Payment).filter(Payment.payment_id == payment_id).first()
        return {c.name: getattr(row, c.name) for c in Payment.__table__.columns} if row else None


def record_refund(order_id: str, refunded_total: int) -> dict | None:
    """Set the order's refunded total (never lowered). Returns the payment
    with "newly_refunded" (paise / cents refunded by this call), or None."""
    with Session(engine) as session:
        row = session.query(Payment).filter(Payment.order_id == order_id).with_for_update().first()
        if row is None:
            return None
        refunded_total = min(int(refunded_total), row.amount)
        newly = max(0, refunded_total - (row.refunded_amount or 0))
        if newly:
            row.refunded_amount = refunded_total
            row.refunded_at = _utcnow()
            row.status = "refunded" if refunded_total >= row.amount else "partially_refunded"
            session.commit()
        return {**{c.name: getattr(row, c.name) for c in Payment.__table__.columns}, "newly_refunded": newly}


def revoke_purchase(order_id: str, item: str, user_id: int, period_days: int | None) -> dict:
    """Take back what a fully refunded order bought: a plan's paid time, or a
    pack's unused units. Returns what was taken back."""
    with Session(engine) as session:
        if item.startswith("pack:"):
            packs = session.query(UsagePack).filter(UsagePack.order_id == order_id).all()
            removed = 0
            for pack in packs:
                removed += pack.quantity - pack.used
                pack.quantity = pack.used
            session.commit()
            return {"pack_units_removed": removed}
        user = session.get(User, user_id)
        slug = item.split(":")[1]
        if user is None or user.plan_slug != slug or not user.plan_paid_until or not period_days:
            return {"plan_days_removed": 0}
        now = _utcnow().replace(tzinfo=None)
        user.plan_paid_until = max(now, user.plan_paid_until - timedelta(days=period_days))
        session.commit()
        return {"plan_days_removed": period_days, "paid_until": _as_utc(user.plan_paid_until).isoformat()}


def get_billing_profile(user_id: int) -> dict:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None:
            return {}
        return {"name": user.billing_name or user.name, "email": user.email, "gstin": user.billing_gstin,
                "address": user.billing_address, "state": user.billing_state}


def save_billing_profile(user_id: int, name: str | None, gstin: str | None, address: str | None, state: str | None) -> dict:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is not None:
            user.billing_name, user.billing_gstin = name or None, gstin or None
            user.billing_address, user.billing_state = address or None, state or None
            session.commit()
    return get_billing_profile(user_id)


def next_invoice_number(series: str) -> tuple[str, int]:
    """The next free number in a series ("AVIR/26-27" -> "AVIR/26-27/00007")."""
    with Session(engine) as session:
        last = session.query(func.max(Invoice.sequence)).filter(Invoice.series == series).scalar() or 0
    return f"{series}/{last + 1:05d}", last + 1


def save_invoice(**fields) -> dict | None:
    """Store an invoice; None if its number was taken meanwhile (the caller retries)."""
    from sqlalchemy.exc import IntegrityError

    with Session(engine) as session:
        row = Invoice(**{**fields, "details": json.dumps(fields["details"], default=str)})
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return None
        return _invoice_dict(row)


def _invoice_dict(row: "Invoice") -> dict:
    data = {c.name: getattr(row, c.name) for c in Invoice.__table__.columns}
    data["details"] = json.loads(row.details or "{}")
    data["issued_at"] = _as_utc(row.issued_at).isoformat()
    return data


def get_invoice(number: str) -> dict | None:
    with Session(engine) as session:
        row = session.query(Invoice).filter(Invoice.number == number).first()
        return _invoice_dict(row) if row else None


def invoices_for_order(order_id: str) -> list[dict]:
    with Session(engine) as session:
        return [_invoice_dict(r) for r in session.query(Invoice).filter(Invoice.order_id == order_id).order_by(Invoice.id)]


def _subscription_dict(row: "Subscription") -> dict:
    data = {c.name: getattr(row, c.name) for c in Subscription.__table__.columns}
    data["created_at"] = _as_utc(row.created_at).isoformat()
    data["cancelled_at"] = _as_utc(row.cancelled_at).isoformat() if row.cancelled_at else None
    return data


def create_subscription(user_id: int, provider: str, provider_subscription_id: str, plan_slug: str, period: str,
                        amount: int, currency: str) -> dict:
    with Session(engine) as session:
        row = Subscription(user_id=user_id, provider=provider, provider_subscription_id=provider_subscription_id,
                           plan_slug=plan_slug, period=period, amount=amount, currency=currency)
        session.add(row)
        session.commit()
        return _subscription_dict(row)


def get_subscription(provider_subscription_id: str) -> dict | None:
    with Session(engine) as session:
        row = session.query(Subscription).filter(Subscription.provider_subscription_id == provider_subscription_id).first()
        return _subscription_dict(row) if row else None


def update_subscription(provider_subscription_id: str, **fields) -> dict | None:
    with Session(engine) as session:
        row = session.query(Subscription).filter(Subscription.provider_subscription_id == provider_subscription_id).first()
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        session.commit()
        return _subscription_dict(row)


def plans_ending_between(start: datetime, end: datetime) -> list[dict]:
    """Paid plans whose paid time ends in [start, end), with whether they renew
    automatically - for the billing reminders."""
    lo, hi = start.astimezone(timezone.utc).replace(tzinfo=None), end.astimezone(timezone.utc).replace(tzinfo=None)
    with Session(engine) as session:
        users = session.query(User).filter(User.plan_paid_until >= lo, User.plan_paid_until < hi,
                                           User.is_active.isnot(False)).all()
        names = dict(session.query(Plan.slug, Plan.name).all())
        out = []
        for user in users:
            sub = (session.query(Subscription)
                   .filter(Subscription.user_id == user.id, Subscription.status == "active",
                           Subscription.plan_slug == user.plan_slug)
                   .order_by(Subscription.created_at.desc()).first())
            out.append({"user_id": user.id, "plan_slug": user.plan_slug, "plan_name": names.get(user.plan_slug, user.plan_slug),
                        "paid_until": _as_utc(user.plan_paid_until), "renewing": sub is not None,
                        "subscription_id": sub.provider_subscription_id if sub else None,
                        "amount": sub.amount if sub else None, "currency": sub.currency if sub else None})
        return out


def list_subscriptions(limit: int = 200) -> list[dict]:
    """Admin -> Payments: subscriptions with the user's email, newest first."""
    with Session(engine) as session:
        rows = session.query(Subscription).filter(Subscription.status != "created") \
            .order_by(Subscription.created_at.desc()).limit(limit).all()
        emails = dict(session.query(User.id, User.email).filter(User.id.in_({r.user_id for r in rows} or {0})).all())
        paid_until = dict(session.query(User.id, User.plan_paid_until).filter(User.id.in_({r.user_id for r in rows} or {0})).all())
        return [{**_subscription_dict(r), "email": emails.get(r.user_id),
                 "paid_until": _as_utc(paid_until[r.user_id]).isoformat() if paid_until.get(r.user_id) else None} for r in rows]


# ── Published posts and how they did (services/metrics_service.py) ────────


def register_published_post(user_id: int, platform: str, platform_post_id: str | None, permalink: str | None,
                            title: str | None, source: str, source_id: int | None = None) -> None:
    """Remember a post the app just published, so its results can be fetched. Once per platform post."""
    if not platform_post_id:
        return  # e.g. LinkedIn through an MCP server: no id to look up
    from sqlalchemy.exc import IntegrityError

    with Session(engine) as session:
        session.add(PublishedPost(user_id=user_id, platform=platform, platform_post_id=str(platform_post_id)[:160],
                                  permalink=(permalink or "")[:500] or None, title=" ".join((title or "").split())[:160] or None,
                                  source=source, source_id=source_id))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()


def published_posts_due_for_metrics(now: datetime, max_age_days: int = 30) -> list[dict]:
    """Posts to refresh: published within max_age_days, never fetched, or last
    fetched over 6 hours ago (first 2 days) / 24 hours ago (after that)."""
    oldest = (now - timedelta(days=max_age_days)).astimezone(timezone.utc).replace(tzinfo=None)
    naive_now = now.astimezone(timezone.utc).replace(tzinfo=None)
    with Session(engine) as session:
        rows = session.query(PublishedPost).filter(PublishedPost.published_at >= oldest).all()
        due = []
        for row in rows:
            fresh = naive_now - row.published_at < timedelta(days=2)
            every = timedelta(hours=6) if fresh else timedelta(hours=24)
            if row.metrics_at is None or naive_now - row.metrics_at >= every:
                due.append({"id": row.id, "user_id": row.user_id, "platform": row.platform,
                            "platform_post_id": row.platform_post_id})
        return due


def save_post_metrics(post_id: int, metrics: dict | None, error: str | None = None) -> None:
    with Session(engine) as session:
        row = session.get(PublishedPost, post_id)
        if row is None:
            return
        row.metrics_at = _utcnow()
        row.metrics_error = (error or "")[:300] or None
        if metrics is not None:
            row.metrics = json.dumps(metrics)
            row.engagement = sum(int(metrics.get(k) or 0) for k in ("likes", "comments", "shares", "saves"))
        session.commit()


def published_posts_for_timing(user_id: int, since: datetime) -> list[dict]:
    """Published posts with fetched results since `since` - for best times to post."""
    lo = since.astimezone(timezone.utc).replace(tzinfo=None)
    with Session(engine) as session:
        rows = session.query(PublishedPost.platform, PublishedPost.published_at, PublishedPost.engagement).filter(
            PublishedPost.user_id == user_id, PublishedPost.published_at >= lo, PublishedPost.metrics_at.isnot(None)).all()
        return [{"platform": p, "published_at": _as_utc(at), "engagement": int(e or 0)} for p, at, e in rows]


def post_analytics(user_id: int, since: datetime, until: datetime | None = None) -> dict:
    """Totals, per platform and top posts for posts published in [since, until)."""
    lo = since.astimezone(timezone.utc).replace(tzinfo=None)
    hi = (until or _utcnow()).astimezone(timezone.utc).replace(tzinfo=None)
    keys = ("likes", "comments", "shares", "saves", "reach", "views")
    with Session(engine) as session:
        rows = session.query(PublishedPost).filter(PublishedPost.user_id == user_id, PublishedPost.published_at >= lo,
                                                   PublishedPost.published_at < hi).all()
        totals = {k: 0 for k in keys}
        platforms: dict[str, dict] = {}
        posts = []
        for row in rows:
            metrics = json.loads(row.metrics) if row.metrics else {}
            per = platforms.setdefault(row.platform, {"posts": 0, "engagement": 0, **{k: 0 for k in keys}})
            per["posts"] += 1
            per["engagement"] += row.engagement
            for k in keys:
                totals[k] += int(metrics.get(k) or 0)
                per[k] += int(metrics.get(k) or 0)
            posts.append({"platform": row.platform, "title": row.title, "permalink": row.permalink,
                          "published_at": _as_utc(row.published_at).isoformat(), "engagement": row.engagement,
                          "metrics": metrics, "updated_at": _as_utc(row.metrics_at).isoformat() if row.metrics_at else None,
                          "error": row.metrics_error})
        posts.sort(key=lambda p: p["engagement"], reverse=True)
        engagement = sum(p["engagement"] for p in posts)
        return {"posts": len(posts), "engagement": engagement, "totals": totals,
                "engagement_rate": round(100 * engagement / totals["reach"], 1) if totals["reach"] else None,
                "platforms": platforms, "top_posts": posts[:5], "all_posts": posts}


def current_subscription(user_id: int) -> dict | None:
    """The user's renewing subscription (active, or active but cancelled at period end)."""
    with Session(engine) as session:
        row = (session.query(Subscription)
               .filter(Subscription.user_id == user_id, Subscription.status.in_(("active", "cancelled")))
               .order_by(Subscription.created_at.desc()).first())
        return _subscription_dict(row) if row else None


def list_payments(limit: int = 100, user_id: int | None = None) -> list[dict]:
    """Admin -> Payments: recent paid / refunded payments with the buyer's email and invoice numbers."""
    with Session(engine) as session:
        query = session.query(Payment).filter(Payment.status != "created")
        if user_id:
            query = query.filter(Payment.user_id == user_id)
        rows = query.order_by(Payment.created_at.desc()).limit(limit).all()
        emails = dict(session.query(User.id, User.email).filter(User.id.in_({r.user_id for r in rows} or {0})).all())
        numbers: dict[str, list] = {}
        for inv in session.query(Invoice).filter(Invoice.order_id.in_([r.order_id for r in rows] or [""])):
            numbers.setdefault(inv.order_id, []).append({"number": inv.number, "kind": inv.kind})
        return [{
            "order_id": r.order_id, "payment_id": r.payment_id, "provider": r.provider, "user_id": r.user_id,
            "email": emails.get(r.user_id), "item": r.item, "description": r.description, "amount": r.amount,
            "tax": r.tax, "currency": r.currency, "status": r.status, "refunded_amount": r.refunded_amount or 0,
            "paid_at": _as_utc(r.paid_at).isoformat() if r.paid_at else None, "invoices": numbers.get(r.order_id, []),
        } for r in rows]


def list_user_payments(user_id: int, limit: int = 20) -> list[dict]:
    with Session(engine) as session:
        rows = session.query(Payment).filter(Payment.user_id == user_id, Payment.paid_at.isnot(None)) \
            .order_by(Payment.paid_at.desc()).limit(limit).all()
        numbers: dict[str, list] = {}
        for inv in session.query(Invoice).filter(Invoice.order_id.in_([r.order_id for r in rows] or [""])):
            numbers.setdefault(inv.order_id, []).append({"number": inv.number, "kind": inv.kind})
        return [{"description": r.description, "amount": r.amount, "tax": r.tax, "currency": r.currency,
                 "status": r.status, "refunded_amount": r.refunded_amount or 0,
                 "paid_at": _as_utc(r.paid_at).isoformat() if r.paid_at else None, "payment_id": r.payment_id,
                 "invoices": numbers.get(r.order_id, [])} for r in rows]


# ── Audit log ─────────────────────────────────────────────────────────────


def log_audit(action: str, actor_user_id: int | None = None, target_user_id: int | None = None,
              details: dict | None = None, ip: str | None = None) -> None:
    """Record a change. Never raises: a failed audit write must not undo the change."""
    try:
        with Session(engine) as session:
            session.add(AuditLog(action=action[:48], actor_user_id=actor_user_id, target_user_id=target_user_id,
                                 details=json.dumps(details, default=str) if details else None, ip=(ip or "")[:64] or None))
            session.commit()
    except Exception as err:  # noqa: BLE001
        logger.warning(f"Audit log write failed ({action}): {err}")


def list_audit_log(limit: int = 100, target_user_id: int | None = None, action: str | None = None) -> list[dict]:
    with Session(engine) as session:
        query = session.query(AuditLog)
        if target_user_id:
            query = query.filter(AuditLog.target_user_id == target_user_id)
        if action:
            query = query.filter(AuditLog.action.like(f"{action}%"))
        rows = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(max(1, min(500, limit))).all()
        ids = {r.actor_user_id for r in rows if r.actor_user_id} | {r.target_user_id for r in rows if r.target_user_id}
        emails = dict(session.query(User.id, User.email).filter(User.id.in_(ids or {0})).all())
        return [{
            "id": r.id, "at": _as_utc(r.created_at).isoformat(), "action": r.action,
            "actor": emails.get(r.actor_user_id) if r.actor_user_id else "system",
            "target": emails.get(r.target_user_id), "target_user_id": r.target_user_id,
            "details": json.loads(r.details) if r.details else None, "ip": r.ip,
        } for r in rows]


# ── Two-step sign-in (auth/two_factor.py) ─────────────────────────────────


def get_totp_state(user_id: int) -> dict:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None:
            return {"enabled": False, "secret": None, "last_counter": None}
        return {"enabled": user.totp_enabled_at is not None, "secret": user.totp_secret,
                "last_counter": user.totp_last_counter}


def set_totp(user_id: int, encrypted_secret: str | None, enabled: bool, last_counter: int | None = None) -> None:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None:
            return
        user.totp_secret = encrypted_secret
        user.totp_enabled_at = _utcnow() if enabled else None
        user.totp_last_counter = last_counter
        session.commit()


def mark_totp_used(user_id: int, counter: int) -> None:
    with Session(engine) as session:
        session.query(User).filter(User.id == user_id).update({"totp_last_counter": counter})
        session.commit()


# ── Pricing plans (PRICING.md) ────────────────────────────────────────────

DEFAULT_PLANS = [
    {"slug": "free", "name": "Free", "sort": 0, "price_inr": 0, "price_usd": 0, "annual_inr": None, "annual_usd": None,
     "best_for": "Trying it out", "posts_month": 10, "images_month": 3, "images_day": 1, "videos_month": 0,
     "videos_day": 0, "video_pack_discount": 0, "brands": 1, "approvals": False, "support": "Help centre"},
    {"slug": "starter", "name": "Starter", "sort": 1, "price_inr": 1599, "price_usd": 19, "annual_inr": 15990,
     "annual_usd": 190, "best_for": "Individuals", "posts_month": 60, "images_month": 10, "images_day": 5,
     "videos_month": 0, "videos_day": 1, "video_pack_discount": 0, "brands": 1, "approvals": False, "support": "Email"},
    {"slug": "growth", "name": "Growth", "sort": 2, "price_inr": 3999, "price_usd": 49, "annual_inr": 39990,
     "annual_usd": 490, "best_for": "Small businesses", "posts_month": 200, "images_month": 25, "images_day": 10,
     "videos_month": 0, "videos_day": 2, "video_pack_discount": 0, "brands": 1, "approvals": True, "support": "Email"},
    {"slug": "pro", "name": "Pro", "sort": 3, "price_inr": 9999, "price_usd": 129, "annual_inr": 99990,
     "annual_usd": 1290, "best_for": "Agencies, medium businesses", "posts_month": 600, "images_month": 60,
     "images_day": 25, "videos_month": 0, "videos_day": 3, "video_pack_discount": 20, "brands": 5, "approvals": True,
     "support": "Priority"},
    {"slug": "enterprise", "name": "Enterprise", "sort": 4, "price_inr": None, "price_usd": None, "annual_inr": None,
     "annual_usd": None, "best_for": "Regulated and large brands", "posts_month": None, "images_month": None,
     "images_day": None, "videos_month": None, "videos_day": None, "video_pack_discount": 0, "brands": None,
     "approvals": True, "support": "Account manager"},
]
PLAN_FIELDS = [c for c in DEFAULT_PLANS[0] if c != "slug"]
PACK_MONTHS = 12


def _seed_plans() -> None:
    """Add any plan in DEFAULT_PLANS that isn't in the table yet. Never
    overwrites a plan an admin has edited."""
    with Session(engine) as session:
        existing = {slug for (slug,) in session.query(Plan.slug)}
        for plan in DEFAULT_PLANS:
            if plan["slug"] not in existing:
                session.add(Plan(**plan))
        session.commit()


def _plan_dict(plan: "Plan") -> dict:
    return {"slug": plan.slug, **{field: getattr(plan, field) for field in PLAN_FIELDS}}


def list_plans() -> list[dict]:
    with Session(engine) as session:
        return [_plan_dict(p) for p in session.query(Plan).order_by(Plan.sort, Plan.slug)]


def update_plan(slug: str, fields: dict) -> dict | None:
    """Admin: change a plan's prices or limits. Unknown fields are ignored."""
    with Session(engine) as session:
        plan = session.get(Plan, slug)
        if plan is None:
            return None
        for field in PLAN_FIELDS:
            if field in fields and field not in ("sort",):
                setattr(plan, field, fields[field])
        session.commit()
        return _plan_dict(plan)


def plan_slug_for(user: "User") -> str:
    """The user's plan: the one an admin assigned, else Enterprise for
    enterprise accounts and DEFAULT_PLAN (Free) for everyone else."""
    if user.plan_slug and not (user.plan_paid_until and _as_utc(user.plan_paid_until) <= datetime.now(timezone.utc)):
        return user.plan_slug
    return "enterprise" if user.account_type == "enterprise" else Config.DEFAULT_PLAN


def _user_plan(session, user: "User") -> dict:
    plan = session.get(Plan, plan_slug_for(user)) or session.get(Plan, "free")
    if plan is None:  # plans not seeded yet (e.g. a test database)
        _seed_plans()
        plan = session.get(Plan, plan_slug_for(user)) or session.get(Plan, "free")
    return _plan_dict(plan)


def plan_period(user: "User", now: datetime | None = None) -> tuple[datetime, datetime]:
    """The current monthly allowance period: from the latest "plan day" of the
    month (the day the plan started; 1st when unknown) to the next one."""
    now = now or datetime.now(timezone.utc)
    anchor_day = (_as_utc(user.plan_started_at).day if user.plan_started_at else 1)

    def on_day(year: int, month: int) -> datetime:
        import calendar

        day = min(anchor_day, calendar.monthrange(year, month)[1])
        return datetime(year, month, day, tzinfo=timezone.utc)

    start = on_day(now.year, now.month)
    if start > now:
        year, month = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
        start = on_day(year, month)
    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return start, on_day(year, month)


def _pack_remaining(session, user_id: int, kind: str, now: datetime) -> int:
    rows = session.query(UsagePack).filter(
        UsagePack.user_id == user_id, UsagePack.kind == kind,
        UsagePack.expires_at > now.replace(tzinfo=None), UsagePack.used < UsagePack.quantity,
    ).all()
    return sum(r.quantity - r.used for r in rows)


def _count_media(session, user_id: int, kinds: tuple, since: datetime) -> int:
    return int(session.query(func.count(ImageGeneration.id)).filter(
        ImageGeneration.user_id == user_id, ImageGeneration.kind.in_(kinds),
        ImageGeneration.created_at >= since.astimezone(timezone.utc).replace(tzinfo=None),
    ).scalar() or 0)


def _use_pack_if_over_allowance(session, user_id: int, kind: str) -> None:
    """Called as an image or video is logged: once the plan's monthly
    allowance is used up, each new one takes one unit from the oldest pack."""
    user = session.get(User, user_id)
    if user is None or user.is_admin:
        return
    plan = _user_plan(session, user)
    limit = plan["videos_month"] if kind == "video" else plan["images_month"]
    if limit is None:
        return  # unlimited
    start, _ = plan_period(user)
    used = _count_media(session, user_id, ("video",) if kind == "video" else ("image", "edit"), start)
    if used <= limit:
        return
    now = _utcnow().replace(tzinfo=None)
    pack = (
        session.query(UsagePack)
        .filter(UsagePack.user_id == user_id, UsagePack.kind == kind, UsagePack.expires_at > now,
                UsagePack.used < UsagePack.quantity)
        .order_by(UsagePack.expires_at.asc(), UsagePack.id.asc())
        .first()
    )
    if pack is not None:
        pack.used += 1


# Features that depend on the plan (what the user can see and use)
PLAN_FEATURES = ("approvals", "video", "multiple_brands")


def plan_features(plan: dict, is_admin: bool = False) -> dict:
    """{feature: True/False} for a plan; admins have everything."""
    if is_admin:
        return {f: True for f in PLAN_FEATURES}
    return {
        "approvals": bool(plan["approvals"]),
        # video comes included, or in packs on plans with a daily video cap above 0
        "video": plan["videos_month"] is None or plan["videos_month"] > 0 or (plan["videos_day"] or 0) > 0,
        "multiple_brands": plan["brands"] is None or plan["brands"] > 1,
    }


def cheapest_plan_with(feature: str) -> dict | None:
    """The lowest plan that includes a feature - for "Upgrade to Growth" messages."""
    for plan in list_plans():
        if plan_features(plan).get(feature):
            return plan
    return None


def get_user_features(user_id: int | None) -> dict:
    """{feature: True/False} for the user's plan, plus "plan" (its slug)."""
    if not user_id:
        return {f: False for f in PLAN_FEATURES}
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None:
            return {f: False for f in PLAN_FEATURES}
        plan = _user_plan(session, user)
        return {**plan_features(plan, bool(user.is_admin)), "plan": plan["slug"], "plan_name": plan["name"]}


def get_plan_usage(user_id: int, now: datetime | None = None) -> dict | None:
    """The user's plan and how much of it is used this period. limits of None = unlimited."""
    now = now or datetime.now(timezone.utc)
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None:
            return None
        plan = _user_plan(session, user)
        start, end = plan_period(user, now)
        posts_used = len(posts_created_between(user_id, start, now + timedelta(seconds=1)))
        images_used = _count_media(session, user_id, ("image", "edit"), start)
        videos_used = _count_media(session, user_id, ("video",), start)
        image_pack = _pack_remaining(session, user_id, "image", now)
        video_pack = _pack_remaining(session, user_id, "video", now)

        def left(limit, used, pack=0):
            if limit is None:
                return None
            return max(0, limit - used) + pack

        return {
            "plan": plan,
            "paid_until": _as_utc(user.plan_paid_until).isoformat() if user.plan_paid_until and user.plan_slug == plan["slug"] else None,
            "features": plan_features(plan, bool(user.is_admin)),
            "is_admin": bool(user.is_admin),
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "posts": {"used": posts_used, "limit": plan["posts_month"], "remaining": left(plan["posts_month"], posts_used)},
            "images": {"used": images_used, "limit": plan["images_month"], "pack_remaining": image_pack,
                       "remaining": left(plan["images_month"], images_used, image_pack)},
            "videos": {"used": videos_used, "limit": plan["videos_month"], "pack_remaining": video_pack,
                       "remaining": left(plan["videos_month"], videos_used, video_pack)},
        }


def assign_plan(user_id: int, slug: str) -> bool:
    """Admin: put the user on a plan. Its monthly allowance starts today."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None or session.get(Plan, slug) is None:
            return False
        if user.plan_slug != slug:
            user.plan_slug = slug
            user.plan_started_at = _utcnow()
        user.plan_paid_until = None  # an admin's assignment has no end date
        session.commit()
        return True


def extend_paid_plan(user_id: int, slug: str, days: int) -> dict | None:
    """A paid plan: the same plan again extends its end date; a different
    plan starts today. Returns {"plan", "paid_until"}."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None or session.get(Plan, slug) is None:
            return None
        now = _utcnow()
        current_end = _as_utc(user.plan_paid_until) if user.plan_paid_until else None
        if user.plan_slug == slug and current_end and current_end > now:
            start = current_end
        else:
            start = now
            user.plan_slug = slug
            user.plan_started_at = now
        user.plan_paid_until = (start + timedelta(days=days)).replace(tzinfo=None)
        session.commit()
        return {"plan": slug, "paid_until": _as_utc(user.plan_paid_until).isoformat()}


def grant_pack(user_id: int, kind: str, quantity: int, note: str | None = None, order_id: str | None = None) -> dict | None:
    """Admin: add prepaid images or videos (kind "image" or "video")."""
    if kind not in ("image", "video") or quantity <= 0:
        return None
    with Session(engine) as session:
        if session.get(User, user_id) is None:
            return None
        pack = UsagePack(user_id=user_id, kind=kind, quantity=int(quantity), note=(note or "")[:200] or None,
                         expires_at=(_utcnow() + timedelta(days=30 * PACK_MONTHS)).replace(tzinfo=None), order_id=order_id)
        session.add(pack)
        session.commit()
        return {"id": pack.id, "kind": kind, "quantity": pack.quantity}


# ── Image limits & model access ───────────────────────────────────────────
IMAGE_LIMIT_DEFAULT = 2  # images per day per user, unless changed in Image Settings
IMAGE_UNLIMITED = -1


def _day_start_utc(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def get_image_settings() -> dict:
    """Global image defaults (Admin -> Image Settings), stored in app_settings."""
    from config import Config

    try:
        models = json.loads(get_setting("image_models", "") or "[]")
    except ValueError:
        models = []
    models = [m for m in models if isinstance(m, dict) and m.get("id")]
    default_model = get_setting("image_model_default", "") or Config.HEYROUTE_IMAGE_MODEL
    if not any(m["id"] == default_model for m in models):
        models.insert(0, {"id": default_model, "price": Config.HEYROUTE_IMAGE_COST_USD})
    try:
        default_limit = int(get_setting("image_limit_default", "") or IMAGE_LIMIT_DEFAULT)
    except ValueError:
        default_limit = IMAGE_LIMIT_DEFAULT
    return {"default_limit": default_limit, "default_model": default_model, "models": models}


def save_image_settings(default_limit: int, default_model: str, models: list[dict]) -> dict:
    save_setting("image_limit_default", str(int(default_limit)))
    save_setting("image_model_default", default_model)
    save_setting("image_models", json.dumps(models))
    return get_image_settings()


def image_model_price(model: str | None) -> float:
    """Price per image for a model (Image Settings), else HEYROUTE_IMAGE_COST_USD."""
    from config import Config

    for m in get_image_settings()["models"]:
        if m["id"] == model:
            try:
                return float(m.get("price"))
            except (TypeError, ValueError):
                break
    return Config.HEYROUTE_IMAGE_COST_USD


def get_image_quota(user_id: int) -> dict:
    """Today's image usage (resets at midnight UTC) and the user's limit/model.
    Admins are unlimited unless an admin set a limit for them explicitly."""
    settings = get_image_settings()
    now = datetime.now(timezone.utc)
    day_start = _day_start_utc(now)
    with Session(engine) as session:
        user = session.get(User, user_id)
        used = (
            session.query(func.count(ImageGeneration.id))
            .filter(
                ImageGeneration.user_id == user_id,
                ImageGeneration.kind.in_(("image", "edit")),
                ImageGeneration.created_at >= day_start.replace(tzinfo=None),
            )
            .scalar()
            or 0
        )
        if user is None:
            return {"used": used, "limit": 0, "unlimited": False, "remaining": 0, "model": settings["default_model"]}
        # Daily cap: an admin's override for this user, else the plan's, else Image Settings
        plan = _user_plan(session, user)
        raw_limit = user.image_limit
        if raw_limit is None:
            if user.is_admin:
                raw_limit = IMAGE_UNLIMITED
            elif plan["images_day"] is None:
                raw_limit = IMAGE_UNLIMITED
            else:
                raw_limit = plan["images_day"]
        unlimited = raw_limit == IMAGE_UNLIMITED
        model = _user_image_model(user, settings)
        resets_at = day_start + timedelta(days=1)
        # Monthly allowance + packs: once both run out, no more images until the next period
        month_left = None
        if not user.is_admin and plan["images_month"] is not None:
            start, _ = plan_period(user, now)
            month_left = max(0, plan["images_month"] - _count_media(session, user_id, ("image", "edit"), start)) \
                + _pack_remaining(session, user_id, "image", now)
        day_left = None if unlimited else max(0, int(raw_limit) - int(used))
        remaining = day_left if month_left is None else (month_left if day_left is None else min(day_left, month_left))
        return {
            "used": int(used),
            "limit": None if unlimited else int(raw_limit),
            "unlimited": unlimited and month_left is None,
            "remaining": remaining,
            "month_remaining": month_left,
            "blocked_by": ("month" if month_left == 0 else ("day" if day_left == 0 else None)),
            "plan": plan["slug"],
            "is_default_limit": user.image_limit is None,
            "model": model,
            "is_default_model": not user.image_model,
            "resets_at": resets_at.isoformat(),
            "resets_in_seconds": int((resets_at - now).total_seconds()),
        }


VIDEO_LIMIT_DEFAULT = 3  # videos per day per user, unless "video_limit_default" is set in app_settings


def get_video_quota(user_id: int) -> dict:
    """Today's video usage (resets at midnight UTC) and the daily limit.
    Admins are unlimited. Same shape as get_image_quota, without a model."""
    try:
        default_limit = int(get_setting("video_limit_default", "") or VIDEO_LIMIT_DEFAULT)
    except ValueError:
        default_limit = VIDEO_LIMIT_DEFAULT
    now = datetime.now(timezone.utc)
    day_start = _day_start_utc(now)
    with Session(engine) as session:
        user = session.get(User, user_id)
        used = (
            session.query(func.count(ImageGeneration.id))
            .filter(
                ImageGeneration.user_id == user_id,
                ImageGeneration.kind == "video",
                ImageGeneration.created_at >= day_start.replace(tzinfo=None),
            )
            .scalar()
            or 0
        )
        unlimited = bool(user and user.is_admin)
        plan = _user_plan(session, user) if user else None
        limit = default_limit if user else 0
        if plan and plan["videos_day"] is not None:
            limit = plan["videos_day"]
        resets_at = day_start + timedelta(days=1)
        # Videos come from the plan's included allowance, then video packs
        month_left = None
        if user and not user.is_admin and plan and plan["videos_month"] is not None:
            start, _ = plan_period(user, now)
            month_left = max(0, plan["videos_month"] - _count_media(session, user_id, ("video",), start)) \
                + _pack_remaining(session, user_id, "video", now)
        day_left = None if unlimited else max(0, int(limit) - int(used))
        remaining = day_left if month_left is None else (month_left if day_left is None else min(day_left, month_left))
        return {
            # False = video is still "coming soon" for this user
            "enabled": bool(user and (user.is_admin or user.video_enabled)),
            "used": int(used),
            "limit": None if unlimited else int(limit),
            "unlimited": unlimited,
            "remaining": remaining,
            "month_remaining": month_left,
            "blocked_by": ("month" if month_left == 0 else ("day" if day_left == 0 else None)),
            "plan": plan["slug"] if plan else None,
            "resets_at": resets_at.isoformat(),
            "resets_in_seconds": int((resets_at - now).total_seconds()),
        }


def _user_image_model(user: "User", settings: dict) -> str:
    """The user's own model while it's still offered in Image Settings, else the default."""
    if user.image_model and any(m["id"] == user.image_model for m in settings["models"]):
        return user.image_model
    return settings["default_model"]


def set_user_image_access(user_id: int, limit: int | None, model: str | None) -> bool:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            return False
        user.image_limit = limit
        user.image_model = model or None
        session.commit()
        return True


def set_user_video_access(user_id: int, enabled: bool) -> bool:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            return False
        user.video_enabled = bool(enabled)
        session.commit()
        return True


INVITATION_TTL = timedelta(days=30)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _invitation_status(row: "UserInvitation") -> str:
    if row.accepted_at:
        return "accepted"
    if row.revoked_at:
        return "revoked"
    if _as_utc(row.expires_at) <= datetime.now(timezone.utc):
        return "expired"
    return "pending"


def _serialize_invitation(row: "UserInvitation", inviter_name: str | None = None) -> dict:
    return {
        "id": row.id,
        "email": row.email,
        "name": row.name,
        "message": row.message,
        "status": _invitation_status(row),
        # The name typed for the email wins over the admin's account name
        "invited_by": row.inviter_name or inviter_name,
        "sent_at": _as_utc(row.sent_at).isoformat() if row.sent_at else None,
        "expires_at": _as_utc(row.expires_at).isoformat() if row.expires_at else None,
        "accepted_at": _as_utc(row.accepted_at).isoformat() if row.accepted_at else None,
        "click_count": row.click_count or 0,
        "first_clicked_at": _as_utc(row.first_clicked_at).isoformat() if row.first_clicked_at else None,
    }


def upsert_invitation(
    email: str,
    name: str | None,
    message: str | None,
    invited_by_user_id: int,
    token: str,
    inviter_name: str | None = None,
) -> dict:
    """Creates an invitation, or refreshes the open one for this email (new
    token + expiry - the previous link stops working). Returns it serialized."""
    email = email.strip().lower()
    now = _utcnow()
    with Session(engine) as session:
        row = (
            session.query(UserInvitation)
            .filter(UserInvitation.email == email, UserInvitation.accepted_at.is_(None))
            .order_by(UserInvitation.created_at.desc())
            .first()
        )
        if row is None:
            row = UserInvitation(email=email)
            session.add(row)
        row.name = (name or "").strip() or row.name
        row.message = (message or "").strip() or None
        row.token = token
        row.invited_by_user_id = invited_by_user_id
        row.inviter_name = (inviter_name or "").strip()[:255] or row.inviter_name
        row.sent_at = now
        row.expires_at = now + INVITATION_TTL
        row.revoked_at = None
        session.commit()
        session.refresh(row)
        inviter = session.get(User, invited_by_user_id)
        return _serialize_invitation(row, inviter.name if inviter else None)


def list_invitations(limit: int = 2000) -> list[dict]:
    with Session(engine) as session:
        rows = session.query(UserInvitation).order_by(UserInvitation.sent_at.desc()).limit(limit).all()
        names = {u.id: u.name for u in session.query(User.id, User.name).all()}
        out = [_serialize_invitation(r, names.get(r.invited_by_user_id)) for r in rows]
        # Opening their preview page counts as opening the invitation
        viewed = {e.lower() for (e,) in session.query(InviteContact.email).join(
            LeadPreview, LeadPreview.contact_id == InviteContact.id).filter(LeadPreview.view_count > 0).all()}
    emails = [i["email"] for i in out]
    sent_messages = latest_invite_messages(emails)
    active = _active_user_emails([i["email"] for i in out if i["status"] == "accepted"])
    for i in out:
        entry = sent_messages.get(i["email"].lower()) or {}
        i["last_email"], i["emails_sent"] = entry.get("latest"), entry.get("count", 0)
        i["stage"] = ("made_post" if i["email"].lower() in active else "signed_up" if i["status"] == "accepted"
                      else "opened" if i["click_count"] or i["email"].lower() in viewed else "invited")
    return out


def get_invitation(invitation_id: int) -> dict | None:
    with Session(engine) as session:
        row = session.get(UserInvitation, invitation_id)
        if not row:
            return None
        inviter = session.get(User, row.invited_by_user_id) if row.invited_by_user_id else None
        return _serialize_invitation(row, inviter.name if inviter else None)


def record_invitation_click(token: str, counted: bool = True) -> dict | None:
    """The invitation behind an emailed link (any status), counting the click
    unless counted=False (a link scanner). None for an unknown token."""
    if not token:
        return None
    with Session(engine) as session:
        row = session.query(UserInvitation).filter(UserInvitation.token == token).first()
        if not row:
            return None
        if counted:
            now = _utcnow()
            row.click_count = (row.click_count or 0) + 1
            row.first_clicked_at = row.first_clicked_at or now
            row.last_clicked_at = now
            session.commit()
            session.refresh(row)
        return {**_serialize_invitation(row), "token": row.token}


def open_invitation_token_for_contact(contact_id: int) -> str | None:
    """The token of the contact's invitation while it can still be used."""
    with Session(engine) as session:
        contact = session.get(InviteContact, contact_id)
        row = session.get(UserInvitation, contact.invitation_id) if contact is not None and contact.invitation_id else None
        return row.token if row is not None and _invitation_status(row) == "pending" else None


def get_open_invitation_by_token(token: str) -> dict | None:
    """The invitation behind a signup link - only while it's still pending."""
    if not token:
        return None
    with Session(engine) as session:
        row = session.query(UserInvitation).filter(UserInvitation.token == token).first()
        if not row or _invitation_status(row) != "pending":
            return None
        inviter = session.get(User, row.invited_by_user_id) if row.invited_by_user_id else None
        return _serialize_invitation(row, inviter.name if inviter else None)


def mark_invitation_accepted(invitation_id: int) -> None:
    with Session(engine) as session:
        row = session.get(UserInvitation, invitation_id)
        if row and not row.accepted_at:
            row.accepted_at = _utcnow()
            session.commit()


# ── Invite contacts (bulk invitations, services/invite_import.py) ───────────
CONTACT_FIELDS = ("name", "company", "job_title", "phone", "city", "country", "industry", "company_size", "website",
                  "description", "notes")
CONTACT_STATUSES = ("not_invited", "invited", "clicked", "joined", "expired", "revoked", "failed", "unsubscribed")
STARTER_TONE = "Starter"  # runs copied from a lead preview at sign-up - not the user's own activity


def member_emails(emails) -> set[str]:
    """The given emails (lower-case) that already have an account."""
    wanted = {e.strip().lower() for e in emails or [] if e}
    if not wanted:
        return set()
    found = set()
    with Session(engine) as session:
        items = sorted(wanted)
        for start in range(0, len(items), 500):
            chunk = items[start:start + 500]
            found.update(e.lower() for (e,) in session.query(User.email).filter(func.lower(User.email).in_(chunk)).all())
    return found


def save_invite_contacts(rows: list[dict], filename: str | None, user_id: int, rows_total: int | None = None,
                         source: str = "upload") -> dict:
    """Adds new contacts and fills in blanks / updates details of known ones
    (an empty cell never wipes a saved value). Returns the batch summary."""
    now = _utcnow()
    added = updated = 0
    with Session(engine) as session:
        batch = None
        if filename:
            batch = InviteBatch(filename=filename[:255], uploaded_by_user_id=user_id, rows_total=rows_total or len(rows), created_at=now)
            session.add(batch)
            session.flush()
        emails = [r["email"] for r in rows]
        existing = {}
        for start in range(0, len(emails), 500):
            for c in session.query(InviteContact).filter(InviteContact.email.in_(emails[start:start + 500])).all():
                existing[c.email] = c
        for row in rows:
            contact = existing.get(row["email"])
            if contact is None:
                contact = InviteContact(email=row["email"], source=source, created_by_user_id=user_id, created_at=now,
                                        batch_id=batch.id if batch else None)
                session.add(contact)
                existing[row["email"]] = contact
                added += 1
            else:
                updated += 1
                if batch:
                    contact.batch_id = batch.id
            for field in CONTACT_FIELDS:
                if row.get(field):
                    setattr(contact, field, row[field])
            if row.get("extra"):  # merged: a later upload adds columns, never drops earlier ones
                try:
                    known = json.loads(contact.extra) if contact.extra else {}
                except ValueError:
                    known = {}
                contact.extra = json.dumps({**known, **row["extra"]})
            contact.updated_at = now
        if batch:
            batch.rows_added, batch.rows_updated = added, updated
            batch.rows_skipped = max(0, (rows_total or len(rows)) - added - updated)
        session.commit()
        return {"batch_id": batch.id if batch else None, "added": added, "updated": updated,
                "skipped": batch.rows_skipped if batch else 0}


def _contact_status(contact: "InviteContact", invitation: "UserInvitation | None", is_member: bool,
                    unsubscribed: bool = False, preview_viewed: bool = False) -> str:
    if is_member or (invitation is not None and invitation.accepted_at):
        return "joined"
    if unsubscribed:
        return "unsubscribed"
    if invitation is not None:
        status = _invitation_status(invitation)
        if contact.last_error and contact.last_invited_at is None:
            return "failed"
        if status == "pending" and ((invitation.click_count or 0) > 0 or preview_viewed):
            return "clicked"
        return {"pending": "invited", "expired": "expired", "revoked": "revoked"}.get(status, "invited")
    return "failed" if contact.last_error else "not_invited"


def _active_user_emails(emails: list[str]) -> set[str]:
    """Of these members, the ones who made something themselves (a run that
    isn't a copied starter post)."""
    wanted = sorted({e.lower() for e in emails if e})
    if not wanted:
        return set()
    with Session(engine) as session:
        users = {}
        for start in range(0, len(wanted), 500):
            for uid, email in session.query(User.id, User.email).filter(func.lower(User.email).in_(wanted[start:start + 500])).all():
                users[uid] = email.lower()
        if not users:
            return set()
        active = {uid for (uid,) in session.query(RunHistory.user_id).filter(
            RunHistory.user_id.in_(list(users)), func.coalesce(RunHistory.tone, "") != STARTER_TONE).distinct().all()}
        return {users[uid] for uid in active}


def list_invite_contacts() -> dict:
    """Every contact with its status, the upload batches, and counts per status."""
    with Session(engine) as session:
        contacts = session.query(InviteContact).order_by(InviteContact.created_at.desc(), InviteContact.id.desc()).all()
        invite_ids = [c.invitation_id for c in contacts if c.invitation_id]
        invitations = {}
        for start in range(0, len(invite_ids), 500):
            for inv in session.query(UserInvitation).filter(UserInvitation.id.in_(invite_ids[start:start + 500])).all():
                invitations[inv.id] = inv
        members = member_emails([c.email for c in contacts])
        active = _active_user_emails(list(members))
        sent_messages = latest_invite_messages([c.email for c in contacts])
        unsubscribed = suppressed_emails([c.email for c in contacts])
        previews = {p.contact_id: p for p in session.query(LeadPreview).all()}
        batches = {b.id: b for b in session.query(InviteBatch).all()}
        from services.invite_import import is_shared_inbox
        out = []
        for c in contacts:
            invitation = invitations.get(c.invitation_id)
            batch = batches.get(c.batch_id)
            preview = previews.get(c.id)
            viewed = bool(preview is not None and preview.view_count)
            out.append({
                "id": c.id, "email": c.email, **{f: getattr(c, f) or "" for f in CONTACT_FIELDS},
                "extra": json.loads(c.extra) if c.extra else {},
                "shared_inbox": is_shared_inbox(c.email),
                "source": c.source, "batch_id": c.batch_id, "batch": batch.filename if batch else None,
                "status": _contact_status(c, invitation, c.email in members, c.email in unsubscribed, viewed),
                "activated": c.email in active,
                "unsubscribed_at": unsubscribed.get(c.email),
                "clicks": (invitation.click_count or 0) if invitation is not None else 0,
                "first_clicked_at": _as_utc(invitation.first_clicked_at).isoformat()
                if invitation is not None and invitation.first_clicked_at else None,
                "preview": _preview_summary(preview),
                "emails_sent": (sent_messages.get(c.email) or {}).get("count", 0),
                "last_email": (sent_messages.get(c.email) or {}).get("latest"),
                "invite_count": c.invite_count or 0,
                "last_invited_at": _as_utc(c.last_invited_at).isoformat() if c.last_invited_at else None,
                "expires_at": _as_utc(invitation.expires_at).isoformat() if invitation is not None and invitation.expires_at else None,
                "last_error": c.last_error,
                "created_at": _as_utc(c.created_at).isoformat(),
            })
        for c in out:
            c["stage"] = ("made_post" if c["activated"] else "signed_up" if c["status"] == "joined"
                          else "opened" if c["clicks"] or (c["preview"] or {}).get("views")
                          else "invited" if c["invite_count"] or c["emails_sent"]
                          else "preview_ready" if (c["preview"] or {}).get("status") == "ready" else "added")
        counts = {status: sum(1 for c in out if c["status"] == status) for status in CONTACT_STATUSES}
        counts["all"] = len(out)
        reached = [c for c in out if c["invite_count"] or c["status"] in ("joined", "clicked", "expired", "revoked")]
        funnel = {
            "contacts": len(out),
            "previews_ready": sum(1 for c in out if (c["preview"] or {}).get("status") == "ready"),
            "invited": len(reached),
            "clicked": sum(1 for c in reached if c["clicks"] or (c["preview"] or {}).get("views")),
            "previews_viewed": sum(1 for c in out if (c["preview"] or {}).get("views")),
            "joined": sum(1 for c in out if c["status"] == "joined"),
            "activated": sum(1 for c in out if c["status"] == "joined" and c["activated"]),
            "unsubscribed": counts["unsubscribed"],
        }
        return {
            "contacts": out, "counts": counts, "funnel": funnel,
            "batches": [{"id": b.id, "filename": b.filename, "created_at": _as_utc(b.created_at).isoformat(),
                         "rows_total": b.rows_total, "rows_added": b.rows_added, "rows_updated": b.rows_updated,
                         "rows_skipped": b.rows_skipped}
                        for b in sorted(batches.values(), key=lambda b: b.created_at, reverse=True)],
        }


def get_invite_contacts(ids) -> list[dict]:
    wanted = [int(i) for i in ids or [] if str(i).isdigit()]
    if not wanted:
        return []
    with Session(engine) as session:
        rows = session.query(InviteContact).filter(InviteContact.id.in_(wanted)).all()
        previews = {p.contact_id: p for p in session.query(LeadPreview).filter(LeadPreview.contact_id.in_(wanted)).all()}
        return [{"id": c.id, "email": c.email, **{f: getattr(c, f) or "" for f in CONTACT_FIELDS},
                 "preview": _preview_summary(previews.get(c.id))} for c in rows]


# ── What was sent (InviteMessage) and how far each person got ──────────────
STAGES = ("added", "preview_ready", "invited", "opened", "signed_up", "made_post")


def record_invite_message(email: str, style: str, subject: str, body: str, status: str = "sent",
                          error: str | None = None, contact_id: int | None = None, invitation_id: int | None = None,
                          sent_by_user_id: int | None = None, sender_name: str | None = None,
                          html: str | None = None) -> int:
    with Session(engine) as session:
        row = InviteMessage(email=(email or "").strip().lower()[:320], style=(style or "designed")[:16],
                            subject=(subject or "")[:300], body=body or "", html=html or None, status=status[:12],
                            error=(error or "")[:500] or None, contact_id=contact_id, invitation_id=invitation_id,
                            sent_by_user_id=sent_by_user_id, sender_name=(sender_name or "")[:255] or None)
        session.add(row)
        session.commit()
        return int(row.id)


def _invite_message_dict(m: "InviteMessage", with_body: bool = True) -> dict:
    out = {"id": m.id, "email": m.email, "style": m.style, "subject": m.subject, "status": m.status, "error": m.error,
           "contact_id": m.contact_id, "invitation_id": m.invitation_id, "sender_name": m.sender_name,
           "sent_at": _as_utc(m.created_at).isoformat()}
    if with_body:
        out["body"] = m.body
        out["html"] = m.html
    return out


def get_invite_message(message_id: int) -> dict | None:
    with Session(engine) as session:
        m = session.get(InviteMessage, message_id)
        return _invite_message_dict(m) if m is not None else None


def latest_invite_messages(emails) -> dict[str, dict]:
    """{email: {"latest": message (no body), "count": n}} - for the lists."""
    wanted = sorted({(e or "").strip().lower() for e in emails or [] if e})
    out: dict[str, dict] = {}
    if not wanted:
        return out
    with Session(engine) as session:
        for start in range(0, len(wanted), 500):
            rows = session.query(InviteMessage).filter(InviteMessage.email.in_(wanted[start:start + 500])) \
                .order_by(InviteMessage.created_at.asc(), InviteMessage.id.asc()).all()
            for m in rows:
                entry = out.setdefault(m.email, {"count": 0, "latest": None})
                entry["count"] += 1
                entry["latest"] = _invite_message_dict(m, with_body=False)
    return out


def invite_contact_journey(contact_id: int) -> dict | None:
    """One person's way through the outreach: every email sent (with its text)
    and the date each stage was reached - added, preview ready, invited,
    opened (link or preview page), signed up, made a post of their own."""
    with Session(engine) as session:
        c = session.get(InviteContact, contact_id)
        if c is None:
            return None
        messages = session.query(InviteMessage).filter(
            (InviteMessage.contact_id == contact_id) | (InviteMessage.email == c.email)
        ).order_by(InviteMessage.created_at.asc(), InviteMessage.id.asc()).all()
        invitation = session.get(UserInvitation, c.invitation_id) if c.invitation_id else None
        preview = session.query(LeadPreview).filter(LeadPreview.contact_id == contact_id).first()
        user = session.query(User).filter(func.lower(User.email) == c.email).first()
        first_post = None
        if user is not None:
            run = session.query(RunHistory).filter(
                RunHistory.user_id == user.id, func.coalesce(RunHistory.tone, "") != STARTER_TONE
            ).order_by(RunHistory.created_at.asc()).first()
            first_post = run.created_at if run is not None else None
        sent = [m for m in messages if m.status == "sent"]
        opened = [d for d in (invitation.first_clicked_at if invitation is not None else None,
                              preview.first_viewed_at if preview is not None else None) if d]
        joined = (invitation.accepted_at if invitation is not None and invitation.accepted_at else None) or \
            (user.created_at if user is not None else None)
        when = {
            "added": c.created_at,
            "preview_ready": preview.generated_at if preview is not None and preview.status == "ready" else None,
            "invited": sent[0].created_at if sent else c.last_invited_at,
            "opened": min(opened) if opened else None,
            "signed_up": joined,
            "made_post": first_post,
        }
        reached = [s for s in STAGES if when[s]]
        return {
            "contact_id": contact_id, "email": c.email,
            "stage": reached[-1] if reached else "added",
            "stages": [{"key": s, "at": _as_utc(when[s]).isoformat() if when[s] else None} for s in STAGES],
            "messages": [_invite_message_dict(m) for m in reversed(messages)],
        }


def _preview_summary(p: "LeadPreview | None") -> dict | None:
    if p is None:
        return None
    return {"status": p.status, "token": p.token, "error": p.error, "views": p.view_count or 0,
            "first_line": p.first_line or "", "cost_usd": round(p.cost_usd or 0.0, 4),
            "first_viewed_at": _as_utc(p.first_viewed_at).isoformat() if p.first_viewed_at else None,
            "generated_at": _as_utc(p.generated_at).isoformat() if p.generated_at else None,
            "claimed": p.claimed_user_id is not None}


# ── Unsubscribes (services/invite_outreach.py) ──────────────────────────────
def suppressed_emails(emails) -> dict[str, str]:
    """{email: when it was suppressed (ISO)} for the given emails."""
    wanted = sorted({(e or "").strip().lower() for e in emails or [] if e})
    out: dict[str, str] = {}
    if not wanted:
        return out
    with Session(engine) as session:
        for start in range(0, len(wanted), 500):
            for row in session.query(EmailSuppression).filter(EmailSuppression.email.in_(wanted[start:start + 500])).all():
                out[row.email] = _as_utc(row.created_at).isoformat()
    return out


def add_suppression(email: str, reason: str = "unsubscribed", source: str | None = None) -> bool:
    """True when newly added (an address already on the list stays as it was)."""
    email = (email or "").strip().lower()
    if not email:
        return False
    with Session(engine) as session:
        if session.query(EmailSuppression.id).filter(EmailSuppression.email == email).first():
            return False
        session.add(EmailSuppression(email=email[:320], reason=reason[:32], source=(source or "")[:64] or None))
        session.commit()
        return True


# ── Lead previews (services/lead_preview_service.py) ────────────────────────
def queue_lead_previews(contact_ids, admin_id: int, refresh: bool = False) -> dict:
    """Marks contacts' previews as pending (new token for new ones). Contacts
    with a ready preview are left alone unless refresh. Returns
    {"queued": [preview ids], "already_ready": n}."""
    import secrets as _secrets

    wanted = [int(i) for i in contact_ids or [] if str(i).isdigit()]
    queued, ready = [], 0
    with Session(engine) as session:
        existing = {p.contact_id: p for p in session.query(LeadPreview).filter(LeadPreview.contact_id.in_(wanted)).all()} if wanted else {}
        contacts = {c.id for c in session.query(InviteContact.id).filter(InviteContact.id.in_(wanted)).all()} if wanted else set()
        for cid in wanted:
            if cid not in contacts:
                continue
            row = existing.get(cid)
            if row is not None and row.status == "running":
                continue
            if row is not None and row.status == "ready" and not refresh:
                ready += 1
                continue
            if row is None:
                row = LeadPreview(contact_id=cid, token=_secrets.token_urlsafe(18))
                session.add(row)
            row.status, row.error, row.created_by_user_id = "pending", None, admin_id
            session.flush()
            queued.append(row.id)
        session.commit()
    return {"queued": queued, "already_ready": ready}


def _lead_preview_dict(p: "LeadPreview", contact: "InviteContact | None") -> dict:
    return {
        "id": p.id, "token": p.token, "status": p.status, "error": p.error, "website": p.website,
        "profile": json.loads(p.profile) if p.profile else {}, "posts": json.loads(p.posts) if p.posts else [],
        "first_line": p.first_line or "", "cost_usd": p.cost_usd or 0.0, "created_by_user_id": p.created_by_user_id,
        "view_count": p.view_count or 0, "images_started": p.images_started_at is not None,
        "claimed_user_id": p.claimed_user_id,
        "contact": ({"id": contact.id, "email": contact.email, **{f: getattr(contact, f) or "" for f in CONTACT_FIELDS},
                     "invitation_id": contact.invitation_id} if contact is not None else None),
    }


def get_lead_preview(preview_id: int | None = None, token: str | None = None) -> dict | None:
    with Session(engine) as session:
        if preview_id is not None:
            p = session.get(LeadPreview, preview_id)
        elif token:
            p = session.query(LeadPreview).filter(LeadPreview.token == token).first()
        else:
            p = None
        if p is None:
            return None
        return _lead_preview_dict(p, session.get(InviteContact, p.contact_id))


def update_lead_preview(preview_id: int, **fields) -> None:
    """profile / posts are given as Python values and stored as JSON."""
    with Session(engine) as session:
        p = session.get(LeadPreview, preview_id)
        if p is None:
            return
        for key, value in fields.items():
            if key in ("profile", "posts"):
                value = json.dumps(value) if value is not None else None
            setattr(p, key, value)
        session.commit()


def set_lead_preview_post(preview_id: int, index: int, **fields) -> None:
    """Updates one draft post (e.g. its image once made) without losing the others."""
    with Session(engine) as session:
        p = session.query(LeadPreview).filter(LeadPreview.id == preview_id).with_for_update().first() \
            if not IS_SQLITE else session.get(LeadPreview, preview_id)
        if p is None or not p.posts:
            return
        posts = json.loads(p.posts)
        if 0 <= index < len(posts):
            posts[index].update(fields)
            p.posts = json.dumps(posts)
            session.commit()


def record_lead_preview_view(token: str) -> dict | None:
    with Session(engine) as session:
        p = session.query(LeadPreview).filter(LeadPreview.token == token).first()
        if p is None:
            return None
        now = _utcnow()
        p.view_count = (p.view_count or 0) + 1
        p.first_viewed_at = p.first_viewed_at or now
        p.last_viewed_at = now
        session.commit()
        return _lead_preview_dict(p, session.get(InviteContact, p.contact_id))


def claim_lead_preview_images(preview_id: int) -> bool:
    """True for the one caller that gets to make this preview's images."""
    with Session(engine) as session:
        changed = session.query(LeadPreview).filter(
            LeadPreview.id == preview_id, LeadPreview.images_started_at.is_(None)
        ).update({"images_started_at": _utcnow()}, synchronize_session=False)
        session.commit()
        return bool(changed)


def mark_lead_preview_claimed(preview_id: int, user_id: int) -> bool:
    """True the first time (a preview is copied into one account only)."""
    with Session(engine) as session:
        changed = session.query(LeadPreview).filter(
            LeadPreview.id == preview_id, LeadPreview.claimed_user_id.is_(None)
        ).update({"claimed_user_id": user_id, "claimed_at": _utcnow()}, synchronize_session=False)
        session.commit()
        return bool(changed)


def lead_preview_images_today() -> int:
    """Preview images made in the last 24 hours (the daily spending cap)."""
    since = _utcnow() - timedelta(hours=24)
    with Session(engine) as session:
        return session.query(ImageGeneration.id).filter(
            ImageGeneration.created_at >= since, ImageGeneration.description.like("Lead preview:%")
        ).count()


def record_contact_invite(contact_id: int, invitation_id: int | None, error: str | None = None) -> None:
    """After a send: the invitation it's linked to, or why the email failed."""
    with Session(engine) as session:
        contact = session.get(InviteContact, contact_id)
        if not contact:
            return
        if invitation_id:
            contact.invitation_id = invitation_id
        if error:
            contact.last_error = error[:500]
        else:
            contact.last_error = None
            contact.invite_count = (contact.invite_count or 0) + 1
            contact.last_invited_at = _utcnow()
        contact.updated_at = _utcnow()
        session.commit()


def delete_invite_contacts(ids) -> int:
    """Removes contacts from the list (any invitation already sent keeps working)."""
    wanted = [int(i) for i in ids or [] if str(i).isdigit()]
    if not wanted:
        return 0
    with Session(engine) as session:
        removed = session.query(InviteContact).filter(InviteContact.id.in_(wanted)).delete(synchronize_session=False)
        session.commit()
        return removed


def revoke_invitation(invitation_id: int) -> bool:
    with Session(engine) as session:
        row = session.get(UserInvitation, invitation_id)
        if not row or row.accepted_at:
            return False
        row.revoked_at = _utcnow()
        session.commit()
        return True


def add_run_cost(run_id: int, amount_usd: float, user_id: int) -> bool:
    """Adds a media generation's cost (e.g. an image) to its run, so it counts
    towards the user's used credits (get_user_usage_stats sums run costs)."""
    if not amount_usd or amount_usd <= 0:
        return False
    with Session(engine) as session:
        row = session.get(RunHistory, run_id)
        if not row or row.user_id != user_id:
            return False
        row.cost_usd = round(float(row.cost_usd or 0.0) + float(amount_usd), 6)
        session.commit()
        return True


def get_history(limit: int = 20, user_id: int | None = None, include_archived: bool = False) -> list[dict]:
    with Session(engine) as session:
        query = session.query(RunHistory).order_by(RunHistory.created_at.desc())
        if user_id is not None:
            query = query.filter(RunHistory.user_id == user_id)
        if not include_archived:
            query = query.filter((RunHistory.is_archived.is_(False)) | (RunHistory.is_archived.is_(None)))
        else:
            query = query.filter(RunHistory.is_archived.is_(True))
        rows = query.limit(limit).all()
        return [_serialize_run(r, truncate_story=True) for r in rows]


def get_run_by_id(run_id: int, user_id: int | None = None) -> dict | None:
    with Session(engine) as session:
        row = session.get(RunHistory, run_id)
        if not row:
            return None
        if user_id is not None and row.user_id != user_id:
            return None
        return _serialize_run(row)


def user_currency(user_id: int | None) -> str:
    """"INR" for users whose brand is in India (brand profile markets), else
    "USD". Display only: credits and costs are stored in USD."""
    if not user_id:
        return "USD"
    with Session(engine) as session:
        row = session.query(UserBrandProfile).filter(UserBrandProfile.user_id == user_id).first()
        if not row:
            return "USD"
        regions = []
        for field in (row.compliance_regions, row.regions_detected):
            try:
                regions += json.loads(field or "[]")
            except (TypeError, ValueError):
                pass
        return "INR" if "India" in regions else "USD"


def format_money(amount_usd: float, currency: str = "USD") -> str:
    """A USD amount as the user should see it: "$2.00" or "₹190.00"."""
    if currency == "INR":
        return f"₹{amount_usd * Config.USD_INR_RATE:,.2f}"
    return f"${amount_usd:,.2f}"


def get_user_usage_stats(user_id: int) -> dict:
    """Return aggregated token, cost metrics, and credit details for a user."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        credit_limit = user.credit_limit if user and user.credit_limit is not None else Config.DEFAULT_CREDIT_USD
        is_admin = user.is_admin if user else False

        result = (
            session.query(
                func.count(RunHistory.id).label("total_runs"),
                func.coalesce(func.sum(RunHistory.tokens_used), 0).label("total_tokens"),
                func.coalesce(func.sum(RunHistory.cost_usd), 0.0).label("total_cost"),
            )
            .filter(RunHistory.user_id == user_id)
            .first()
        )

        total_runs = result.total_runs if result else 0
        total_tokens = int(result.total_tokens) if result else 0
        used_cost = float(result.total_cost) if result else 0.0
        used_cost += _media_charges_total(session, user_id)

        remaining = max(0.0, credit_limit - used_cost)

        # Check pending request
        pending_req = (
            session.query(CreditRequest)
            .filter(CreditRequest.user_id == user_id, CreditRequest.status == "pending")
            .first()
        )

        plan = _user_plan(session, user) if user else None
        return {
            "total_runs": total_runs,
            "total_tokens": total_tokens,
            "total_cost_usd": round(used_cost, 6),
            # The credit limit caps spending on the Free plan only; paid plans
            # are limited by their monthly allowances instead (get_plan_usage)
            "plan": plan["slug"] if plan else None,
            "plan_name": plan["name"] if plan else None,
            "plan_features": plan_features(plan, is_admin) if plan else {},
            "credit_capped": bool(plan and plan["slug"] == "free" and not is_admin),
            "used_credits": round(used_cost, 4),
            "credit_limit": round(credit_limit, 2),
            "remaining_credits": round(remaining, 4),
            # How the page shows these USD amounts (user_currency / format_money)
            "currency": user_currency(user_id),
            "usd_inr_rate": Config.USD_INR_RATE,
            "is_admin": is_admin,
            "has_pending_request": pending_req is not None,
            "pending_request": (
                {
                    "id": pending_req.id,
                    "requested_amount": pending_req.requested_amount,
                    "reason": pending_req.reason,
                    "created_at": pending_req.created_at.strftime("%Y-%m-%d %H:%M"),
                }
                if pending_req
                else None
            ),
        }


def assign_orphan_runs_to_user(email: str) -> dict:
    """Assign all runs without a user to the account matching email."""
    user = get_user_by_email(email)
    if not user:
        raise ValueError(f"No user found for email: {email}")

    with engine.begin() as conn:
        # SCHEMA is a fixed module-level constant, not user input; :uid is bound separately.
        result = conn.execute(
            text(f'UPDATE "{SCHEMA}".run_history SET user_id = :uid WHERE user_id IS NULL'),  # nosec B608
            {"uid": user.id},
        )
        updated = result.rowcount or 0

    return {
        "email": user.email,
        "user_id": user.id,
        "assigned_runs": updated,
        "total_runs": len(get_history(limit=1000, user_id=user.id)),  # type: ignore
    }


# ── Credit & Admin Database Helper Functions ───────────────────────────────


def create_credit_request(user_id: int, requested_amount: float, reason: str = "") -> dict:
    """Create a new credit extension request for a user."""
    with Session(engine) as session:
        # Check if there is already a pending request
        existing = (
            session.query(CreditRequest)
            .filter(CreditRequest.user_id == user_id, CreditRequest.status == "pending")
            .first()
        )
        if existing:
            existing.requested_amount = requested_amount  # type: ignore
            existing.reason = reason  # type: ignore
            session.commit()
            return {
                "id": existing.id,
                "requested_amount": existing.requested_amount,
                "reason": existing.reason,
                "status": existing.status,
                "updated": True,
            }

        req = CreditRequest(user_id=user_id, requested_amount=requested_amount, reason=reason, status="pending")
        session.add(req)
        session.commit()
        session.refresh(req)
        return {
            "id": req.id,
            "requested_amount": req.requested_amount,
            "reason": req.reason,
            "status": req.status,
            "created_at": req.created_at.strftime("%Y-%m-%d %H:%M"),
        }


def get_user_credit_requests(user_id: int) -> list[dict]:
    with Session(engine) as session:
        rows = (
            session.query(CreditRequest)
            .filter(CreditRequest.user_id == user_id)
            .order_by(CreditRequest.created_at.desc())
            .all()
        )
        return [
            {
                "id": r.id,
                "requested_amount": r.requested_amount,
                "reason": r.reason,
                "status": r.status,
                "created_at": r.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for r in rows
        ]


def get_all_credit_requests(status_filter: str | None = None) -> list[dict]:
    """Return all credit extension requests with user details (for admin)."""
    with Session(engine) as session:
        query = session.query(CreditRequest, User).join(User, CreditRequest.user_id == User.id)
        if status_filter:
            query = query.filter(CreditRequest.status == status_filter)
        query = query.order_by(CreditRequest.created_at.desc())

        results = []
        for req, user in query.all():
            results.append(
                {
                    "id": req.id,
                    "user_id": user.id,
                    "user_name": user.name,
                    "user_email": user.email,
                    "current_limit": user.credit_limit,
                    "requested_amount": req.requested_amount,
                    "reason": req.reason,
                    "status": req.status,
                    "created_at": req.created_at.strftime("%Y-%m-%d %H:%M"),
                }
            )
        return results


def approve_credit_request(request_id: int) -> dict | None:
    """Approve credit request and increase user's credit_limit by requested_amount."""
    with Session(engine) as session:
        req = session.get(CreditRequest, request_id)
        if not req or req.status != "pending":
            return None

        user = session.get(User, req.user_id)
        if not user:
            return None

        req.status = "approved"
        user.credit_limit = (user.credit_limit or 0.0) + req.requested_amount
        session.commit()
        return {
            "request_id": req.id,
            "user_id": user.id,
            "user_email": user.email,
            "user_name": user.name,
            "requested_amount": round(req.requested_amount, 2),
            "new_credit_limit": round(user.credit_limit, 2),
            "status": "approved",
        }


def reject_credit_request(request_id: int) -> dict | None:
    """Reject a credit extension request."""
    with Session(engine) as session:
        req = session.get(CreditRequest, request_id)
        if not req or req.status != "pending":
            return None

        req.status = "rejected"
        user = session.get(User, req.user_id)
        session.commit()
        return {
            "request_id": req.id,
            "user_id": req.user_id,
            "user_email": user.email if user else None,
            "user_name": user.name if user else None,
            "requested_amount": round(req.requested_amount, 2),
            "credit_limit": round(user.credit_limit or 0.0, 2) if user else None,
            "status": "rejected",
        }


def update_user_credit_limit(
    user_id: int, new_limit: float | None = None, add_amount: float | None = None
) -> dict | None:
    """Update or add to a user's credit limit (admin action)."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            return None

        if new_limit is not None:
            user.credit_limit = max(0.0, new_limit)
        elif add_amount is not None:
            user.credit_limit = max(0.0, (user.credit_limit or 0.0) + add_amount)

        session.commit()
        return {
            "user_id": user.id,
            "user_name": user.name,
            "user_email": user.email,
            "credit_limit": round(user.credit_limit, 2),
        }


_SELF_SERVE_TYPES = ("individual", "small", "medium")


def _brand_status(user: "User", profile: "UserBrandProfile | None") -> dict:
    """Where a user is with their brand configuration (Admin -> Users).
    status: configured | analyzing | failed | not_set_up | not_onboarded | company"""
    if user.account_type == "enterprise" or (user.is_admin and not user.account_type):
        # Enterprise accounts (and staff admins) use the company-wide Brand Configuration (AppSetting)
        return {"status": "company"}
    if profile is not None:
        return {
            "status": "configured",
            "company_name": profile.company_name,
            "website": profile.website,
            "analyzed_at": profile.analyzed_at.strftime("%Y-%m-%d") if profile.analyzed_at else None,
            "compliance_confirmed": profile.compliance_confirmed_at is not None,
        }
    if user.brand_scan_status == "running":
        return {"status": "analyzing", "website": user.company_website}
    if user.brand_scan_status == "failed":
        return {"status": "failed", "website": user.company_website, "error": user.brand_scan_error}
    if user.account_type in _SELF_SERVE_TYPES and user.onboarding_completed:
        return {"status": "not_set_up", "website": user.company_website}
    return {"status": "not_onboarded"}


def _image_access_summary(user: "User", used_today: int, settings: dict) -> dict:
    """Same rules as get_image_quota, for the admin users table."""
    limit = user.image_limit
    if limit is None:
        limit = IMAGE_UNLIMITED if user.is_admin else settings["default_limit"]
    return {
        "used_today": used_today,
        "limit": None if limit == IMAGE_UNLIMITED else limit,
        "unlimited": limit == IMAGE_UNLIMITED,
        "custom_limit": user.image_limit,  # None = default
        "model": _user_image_model(user, settings),
        "custom_model": user.image_model,  # None = default
        "video_enabled": bool(user.is_admin or user.video_enabled),
    }


def get_all_users_credit_summary() -> list[dict]:
    """Return credit summaries for all registered users (for admin management)."""
    with Session(engine) as session:
        users = session.query(User).order_by(User.created_at.asc()).all()
        profiles = {p.user_id: p for p in session.query(UserBrandProfile).all()}
        media_costs = dict(
            session.query(ImageGeneration.user_id, func.coalesce(func.sum(ImageGeneration.cost_usd), 0.0))
            .filter(ImageGeneration.run_id.is_(None))
            .group_by(ImageGeneration.user_id)
            .all()
        )
        # Image access: today's count per user (resets midnight UTC) + limits/models
        image_settings = get_image_settings()
        images_today = dict(
            session.query(ImageGeneration.user_id, func.count(ImageGeneration.id))
            .filter(
                ImageGeneration.kind.in_(("image", "edit")),
                ImageGeneration.created_at >= _day_start_utc().replace(tzinfo=None),
            )
            .group_by(ImageGeneration.user_id)
            .all()
        )
        summaries = []
        for u in users:
            # Query usage cost for this user
            cost_res = (
                session.query(
                    func.coalesce(func.sum(RunHistory.cost_usd), 0.0).label("used_cost"),
                    func.count(RunHistory.id).label("total_runs"),
                )
                .filter(RunHistory.user_id == u.id)
                .first()
            )

            used_cost = float(cost_res.used_cost) if cost_res else 0.0
            used_cost += float(media_costs.get(u.id, 0.0))
            total_runs = cost_res.total_runs if cost_res else 0
            limit = u.credit_limit if u.credit_limit is not None else Config.DEFAULT_CREDIT_USD
            remaining = max(0.0, limit - used_cost)

            # Check pending request
            has_pending = (
                session.query(CreditRequest)
                .filter(CreditRequest.user_id == u.id, CreditRequest.status == "pending")
                .first()
                is not None
            )

            summaries.append(
                {
                    "id": u.id,
                    "name": u.name,
                    "email": u.email,
                    "is_admin": u.is_admin,
                    "is_active": bool(getattr(u, "is_active", True)),
                    "account_type": u.account_type,
                    "company_website": u.company_website,
                    "onboarding_completed": u.onboarding_completed,
                    "email_verified": u.email_verified,
                    "credit_limit": round(limit, 2),
                    "used_credits": round(used_cost, 4),
                    "remaining_credits": round(remaining, 4),
                    "total_runs": total_runs,
                    "has_pending_request": has_pending,
                    "created_at": u.created_at.strftime("%Y-%m-%d"),
                    "brand": _brand_status(u, profiles.get(u.id)),
                    "images": _image_access_summary(u, int(images_today.get(u.id, 0)), image_settings),
                    "plan": plan_slug_for(u),
                }
            )
        return summaries


def run_media_summary(content: dict | None) -> dict:
    """Image/video URLs generated in a run (one per platform, shared images once)."""
    images, videos = [], []
    for key, pdata in (content or {}).items():
        if str(key).startswith("_") or not isinstance(pdata, dict):
            continue
        media = pdata.get("media") or {}
        img = (media.get("image") or {}).get("url") if isinstance(media.get("image"), dict) else None
        vid = (media.get("video") or {}).get("url") if isinstance(media.get("video"), dict) else None
        if img and img not in images:
            images.append(img)
        if vid and vid not in videos:
            videos.append(vid)
    return {"images": images, "videos": videos}


COST_HISTORY_KINDS = ("all", "runs", "runs_with_images", "charges")
COST_HISTORY_SORTS = ("newest", "oldest", "cost")


def _run_history_row(run: "RunHistory", user: "User | None") -> dict:
    story = run.story or ""
    try:
        content = json.loads(run.content) if run.content else {}
    except (TypeError, ValueError):
        content = {}
    return {
        "id": run.id,
        "user_id": run.user_id,
        "user_name": user.name if user else "Unknown",
        "user_email": user.email if user else "N/A",
        "timestamp": run.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "story": story[:160] + "..." if len(story) > 160 else story,
        "tone": run.tone,
        "platforms": json.loads(run.platforms) if run.platforms else [],
        "tokens_used": run.tokens_used or 0,
        "cost_usd": round(run.cost_usd or 0.0, 6),
        "media": run_media_summary(content),
        "_sort": (run.created_at, run.id),
    }


def _charge_history_row(charge: "ImageGeneration", user: "User | None") -> dict:
    return {
        "id": None,
        "charge_id": charge.id,
        "kind": "media_charge",
        "user_id": charge.user_id,
        "user_name": user.name if user else "Unknown",
        "user_email": user.email if user else "N/A",
        "timestamp": charge.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "story": charge.description or f"{charge.kind.capitalize()} generation",
        "tone": None,
        "platforms": [charge.platform] if charge.platform else [],
        "tokens_used": 0,
        "cost_usd": round(charge.cost_usd or 0.0, 6),
        "media": {"images": [charge.media_url] if charge.media_url and charge.kind in ("image", "edit") else [],
                  "videos": [charge.media_url] if charge.media_url and charge.kind == "video" else []},
        "_sort": (charge.created_at, -charge.id),
    }


def get_global_cost_history(
    page: int = 1,
    page_size: int = 25,
    q: str | None = None,
    user_id: int | None = None,
    kind: str = "all",
    platform: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    min_cost: float | None = None,
    sort: str = "newest",
) -> dict:
    """Admin -> Global Cost History: runs plus image/video charges made outside
    a run (e.g. Analysis Dashboard), filtered and paged in the database.

    q matches the user's name or email, the prompt, or "#<run id>".
    kind: all | runs | runs_with_images | charges. date_to is exclusive (the
    caller passes the day after the last day wanted). Returns the page's rows,
    the total number of matching rows, and their summed cost and tokens."""
    from sqlalchemy import and_, exists

    kind = kind if kind in COST_HISTORY_KINDS else "all"
    sort = sort if sort in COST_HISTORY_SORTS else "newest"
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    like = f"%{q.strip()}%" if q and q.strip() else None
    run_number = int(q.strip().lstrip("#")) if q and q.strip().lstrip("#").isdigit() else None

    def run_filters(query):
        if user_id:
            query = query.filter(RunHistory.user_id == user_id)
        if like:
            conds = [User.email.ilike(like), User.name.ilike(like), RunHistory.story.ilike(like)]
            if run_number is not None:
                conds.append(RunHistory.id == run_number)
            query = query.filter(or_(*conds))
        if date_from:
            query = query.filter(RunHistory.created_at >= date_from)
        if date_to:
            query = query.filter(RunHistory.created_at < date_to)
        if min_cost is not None:
            query = query.filter(RunHistory.cost_usd >= min_cost)
        if platform:
            query = query.filter(RunHistory.platforms.like(f'%"{platform}"%'))
        if kind == "runs_with_images":
            # Images logged against the run (image limits), or saved in its
            # content (runs from before images were logged)
            query = query.filter(or_(
                exists().where(and_(ImageGeneration.run_id == RunHistory.id, ImageGeneration.kind.in_(("image", "edit")))),
                RunHistory.content.like('%"image": {"url"%'),
            ))
        return query

    def charge_filters(query):
        query = query.filter(ImageGeneration.run_id.is_(None))
        if user_id:
            query = query.filter(ImageGeneration.user_id == user_id)
        if like:
            query = query.filter(or_(User.email.ilike(like), User.name.ilike(like), ImageGeneration.description.ilike(like)))
        if date_from:
            query = query.filter(ImageGeneration.created_at >= date_from)
        if date_to:
            query = query.filter(ImageGeneration.created_at < date_to)
        if min_cost is not None:
            query = query.filter(ImageGeneration.cost_usd >= min_cost)
        if platform:
            query = query.filter(ImageGeneration.platform == platform)
        return query

    include_runs = kind in ("all", "runs", "runs_with_images")
    include_charges = kind in ("all", "charges")
    # Enough rows from each source to cut this page out of their merge
    need = page * page_size
    run_order = {"newest": (RunHistory.created_at.desc(), RunHistory.id.desc()),
                 "oldest": (RunHistory.created_at.asc(), RunHistory.id.asc()),
                 "cost": (RunHistory.cost_usd.desc(), RunHistory.id.desc())}[sort]
    charge_order = {"newest": (ImageGeneration.created_at.desc(), ImageGeneration.id.desc()),
                    "oldest": (ImageGeneration.created_at.asc(), ImageGeneration.id.asc()),
                    "cost": (ImageGeneration.cost_usd.desc(), ImageGeneration.id.desc())}[sort]

    rows: list[dict] = []
    total, total_cost, total_tokens = 0, 0.0, 0
    with Session(engine) as session:
        if include_runs:
            base = run_filters(session.query(RunHistory, User).outerjoin(User, RunHistory.user_id == User.id))
            rows += [_run_history_row(r, u) for r, u in base.order_by(*run_order).limit(need).all()]
            count, cost, tokens = run_filters(
                session.query(
                    func.count(RunHistory.id),
                    func.coalesce(func.sum(RunHistory.cost_usd), 0.0),
                    func.coalesce(func.sum(RunHistory.tokens_used), 0),
                ).select_from(RunHistory).outerjoin(User, RunHistory.user_id == User.id)
            ).one()
            total, total_cost, total_tokens = total + int(count), total_cost + float(cost), total_tokens + int(tokens)
        if include_charges:
            base = charge_filters(session.query(ImageGeneration, User).outerjoin(User, ImageGeneration.user_id == User.id))
            rows += [_charge_history_row(c, u) for c, u in base.order_by(*charge_order).limit(need).all()]
            count, cost = charge_filters(
                session.query(func.count(ImageGeneration.id), func.coalesce(func.sum(ImageGeneration.cost_usd), 0.0))
                .select_from(ImageGeneration).outerjoin(User, ImageGeneration.user_id == User.id)
            ).one()
            total, total_cost = total + int(count), total_cost + float(cost)

    if sort == "cost":
        rows.sort(key=lambda h: (h["cost_usd"], h["_sort"][0]), reverse=True)
    else:
        rows.sort(key=lambda h: h["_sort"][0], reverse=(sort == "newest"))
    start = (page - 1) * page_size
    page_rows = rows[start:start + page_size]
    for h in page_rows:
        h.pop("_sort", None)
    return {
        "history": page_rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
        "filtered": {"count": total, "cost_usd": round(total_cost, 6), "tokens": total_tokens},
    }


# ── SOCIAL ACCOUNTS & POST SCHEDULING HELPERS ─────────────────────────


def get_user_social_accounts(user_id: int) -> list[dict]:
    """Fetch all connected social media accounts for a user."""
    with Session(engine) as session:
        accounts = session.query(SocialAccount).filter(SocialAccount.user_id == user_id).all()
        result = []
        for acc in accounts:
            result.append(
                {
                    "id": acc.id,
                    "platform": acc.platform,
                    "account_name": acc.account_name,
                    "account_id": acc.account_id,
                    "connection_type": getattr(acc, "connection_type", "direct") or "direct",
                    "mcp_endpoint": getattr(acc, "mcp_endpoint", None),
                    "mcp_tool_name": getattr(acc, "mcp_tool_name", "linkedin_publish_post") or "linkedin_publish_post",
                    "status": acc.status,
                    "connected_via": getattr(acc, "connected_via", None) or "manual",
                    "token_expires_at": _as_utc(acc.token_expires_at).isoformat() if getattr(acc, "token_expires_at", None) else None,
                    "expired": bool(getattr(acc, "token_expires_at", None) and acc.token_expires_at <= _utcnow().replace(tzinfo=None)),
                    "updated_at": acc.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        return result


def save_social_account(
    user_id: int,
    platform: str,
    account_name: str,
    account_id: str | None = None,
    access_token: str | None = None,
    refresh_token: str | None = None,
    connection_type: str = "direct",
    mcp_endpoint: str | None = None,
    mcp_token: str | None = None,
    mcp_tool_name: str | None = None,
    connected_via: str = "manual",
    token_expires_at: datetime | None = None,
) -> dict:
    """Create or update a connected social media account with optional MCP support.
    connected_via / token_expires_at describe the access token being saved."""
    with Session(engine) as session:
        acc = (
            session.query(SocialAccount)
            .filter(SocialAccount.user_id == user_id, SocialAccount.platform == platform)
            .first()
        )

        if not acc:
            acc = SocialAccount(
                user_id=user_id,
                platform=platform,
                account_name=account_name,
                account_id=account_id,
                access_token=access_token,
                refresh_token=refresh_token,
                connection_type=connection_type or "direct",
                mcp_endpoint=mcp_endpoint,
                mcp_token=mcp_token,
                mcp_tool_name=mcp_tool_name or "linkedin_publish_post",
                connected_via=connected_via,
                token_expires_at=token_expires_at,
                status="connected",
            )
            session.add(acc)
        else:
            acc.account_name = account_name
            if account_id:
                acc.account_id = account_id
            if access_token:
                acc.access_token = access_token
                acc.connected_via = connected_via  # a new token: how it came, and when it runs out
                acc.token_expires_at = token_expires_at
            if refresh_token:
                acc.refresh_token = refresh_token
            if connection_type:
                acc.connection_type = connection_type
            if mcp_endpoint is not None:
                acc.mcp_endpoint = mcp_endpoint
            if mcp_token is not None:
                acc.mcp_token = mcp_token
            if mcp_tool_name is not None:
                acc.mcp_tool_name = mcp_tool_name
            acc.status = "connected"
            acc.updated_at = datetime.now(timezone.utc)

        session.commit()
        return {
            "id": acc.id,
            "platform": acc.platform,
            "account_name": acc.account_name,
            "account_id": acc.account_id,
            "connection_type": getattr(acc, "connection_type", "direct"),
            "mcp_endpoint": getattr(acc, "mcp_endpoint", None),
            "mcp_tool_name": getattr(acc, "mcp_tool_name", "linkedin_publish_post"),
            "status": acc.status,
        }


def expiring_social_accounts(before: datetime) -> list[dict]:
    """Connected accounts whose sign-in runs out by `before` (naive UTC) - or already has."""
    with Session(engine) as session:
        rows = (
            session.query(SocialAccount)
            .filter(SocialAccount.status == "connected", SocialAccount.token_expires_at.isnot(None),
                    SocialAccount.token_expires_at <= before)
            .all()
        )
        return [{"id": r.id, "user_id": r.user_id, "platform": r.platform, "account_name": r.account_name,
                 "token_expires_at": r.token_expires_at} for r in rows]


# ── One-click connect: accounts waiting for the user's choice ───────────────


def create_oauth_pending(user_id: int, provider: str, options: list[dict], ttl_minutes: int) -> str:
    """Stores the options (with their tokens) and returns the id the page asks with. Older ones are cleared."""
    pending_id = secrets.token_urlsafe(32)
    with Session(engine) as session:
        cutoff = _utcnow().replace(tzinfo=None) - timedelta(minutes=ttl_minutes)
        session.query(OAuthPending).filter(or_(OAuthPending.created_at < cutoff, OAuthPending.user_id == user_id)).delete(synchronize_session=False)
        session.add(OAuthPending(id=pending_id, user_id=user_id, provider=provider, payload=json.dumps(options),
                                 created_at=_utcnow().replace(tzinfo=None)))
        session.commit()
    return pending_id


def get_oauth_pending(user_id: int, pending_id: str, ttl_minutes: int) -> dict | None:
    with Session(engine) as session:
        row = session.get(OAuthPending, str(pending_id or "")[:64])
        if not row or row.user_id != user_id:
            return None
        if row.created_at < _utcnow().replace(tzinfo=None) - timedelta(minutes=ttl_minutes):
            return None
        return {"provider": row.provider, "options": json.loads(row.payload)}


def delete_oauth_pending(pending_id: str) -> None:
    with Session(engine) as session:
        session.query(OAuthPending).filter(OAuthPending.id == str(pending_id or "")[:64]).delete(synchronize_session=False)
        session.commit()


def disconnect_social_account(user_id: int, platform: str) -> bool:
    """Set social account status to disconnected and delete its tokens (the
    Data Deletion page, legal/data-deletion.md, promises this)."""
    with Session(engine) as session:
        acc = (
            session.query(SocialAccount)
            .filter(SocialAccount.user_id == user_id, SocialAccount.platform == platform)
            .first()
        )
        if acc:
            acc.status = "disconnected"
            acc.access_token = acc.refresh_token = acc.mcp_token = None
            acc.updated_at = datetime.now(timezone.utc)
            session.commit()
            return True
        return False


def create_scheduled_post(
    user_id: int, platforms: list, scheduled_at: datetime, content_json: dict, run_id: int | None = None
) -> dict:
    """Schedule a post for future publishing."""
    with Session(engine) as session:
        post = ScheduledPost(
            user_id=user_id,
            run_id=run_id,
            platforms=json.dumps(platforms) if isinstance(platforms, list) else str(platforms),
            scheduled_at=scheduled_at,
            status="pending",
            content_json=json.dumps(content_json) if isinstance(content_json, dict) else str(content_json),
        )
        session.add(post)
        session.commit()
        return {
            "id": post.id,
            "platforms": platforms,
            "scheduled_at": post.scheduled_at.strftime("%Y-%m-%d %H:%M:%S"),
            "scheduled_at_iso": _as_utc(post.scheduled_at).isoformat(),
            "status": post.status,
        }


def _scheduled_post_dict(row: "ScheduledPost") -> dict:
    """A scheduled post for the pages. Older posts hold one caption for every
    platform; newer ones (services/schedule_service.py) an item per platform."""
    try:
        platforms = json.loads(row.platforms)
    except (TypeError, ValueError):
        platforms = [p.strip() for p in (row.platforms or "").split(",") if p.strip()]
    try:
        content = json.loads(row.content_json)
    except (TypeError, ValueError):
        content = {}
    content = content if isinstance(content, dict) else {}
    items = content.get("items") if isinstance(content.get("items"), list) else []
    first = items[0] if items else {}
    title = first.get("caption") or content.get("caption") or content.get("story") or "Post"
    images = first.get("images") or ([content["image_url"]] if content.get("image_url") else [])
    return {
        "id": row.id,
        "user_id": row.user_id,
        "run_id": row.run_id,
        "platforms": platforms if isinstance(platforms, list) else [],
        # kept as it was for older pages; scheduled_at_iso is the one to show (UTC with its offset)
        "scheduled_at": row.scheduled_at.strftime("%Y-%m-%d %H:%M:%S"),
        "scheduled_at_iso": _as_utc(row.scheduled_at).isoformat(),
        "status": row.status,
        "content_json": content,
        "content": content,
        "items": items,
        "results": content.get("results") if isinstance(content.get("results"), dict) else {},
        "story": content.get("story") or content.get("caption") or "Campaign Post",
        "title": " ".join(str(title).split())[:140],
        "thumb": images[0] if images else None,
        "media": "carousel" if any(i.get("media") == "carousel" for i in items) else ("image" if images else "text"),
        "conversation_id": (content.get("source") or {}).get("conversation_id") if isinstance(content.get("source"), dict) else None,
        "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S"),
    }


def get_user_scheduled_posts(user_id: int) -> list[dict]:
    """Retrieve upcoming and past scheduled posts for a user."""
    with Session(engine) as session:
        posts = (
            session.query(ScheduledPost)
            .filter(ScheduledPost.user_id == user_id)
            .order_by(ScheduledPost.scheduled_at.asc())
            .all()
        )
        return [_scheduled_post_dict(p) for p in posts]


def get_scheduled_post(user_id: int, post_id: int) -> dict | None:
    with Session(engine) as session:
        row = session.query(ScheduledPost).filter(ScheduledPost.id == post_id, ScheduledPost.user_id == user_id).first()
        return _scheduled_post_dict(row) if row else None


def move_scheduled_post(user_id: int, post_id: int, scheduled_at: datetime) -> bool:
    """A new time (naive UTC) for a post that is still waiting."""
    with Session(engine) as session:
        changed = (
            session.query(ScheduledPost)
            .filter(ScheduledPost.id == post_id, ScheduledPost.user_id == user_id, ScheduledPost.status == "pending")
            .update({ScheduledPost.scheduled_at: scheduled_at}, synchronize_session=False)
        )
        session.commit()
        return bool(changed)


def due_scheduled_posts(now: datetime) -> list[dict]:
    """Waiting posts whose time (naive UTC) has come and that hold an item per platform."""
    with Session(engine) as session:
        rows = (
            session.query(ScheduledPost)
            .filter(ScheduledPost.status == "pending", ScheduledPost.scheduled_at <= now)
            .order_by(ScheduledPost.scheduled_at.asc())
            .all()
        )
        return [post for post in (_scheduled_post_dict(r) for r in rows) if post["items"]]


def claim_scheduled_post(post_id: int) -> bool:
    """pending -> publishing, in one statement: True for the one caller that gets to publish it."""
    with Session(engine) as session:
        changed = (
            session.query(ScheduledPost)
            .filter(ScheduledPost.id == post_id, ScheduledPost.status == "pending")
            .update({ScheduledPost.status: "publishing"}, synchronize_session=False)
        )
        session.commit()
        return bool(changed)


def finish_scheduled_post(post_id: int, status: str, content: dict) -> None:
    with Session(engine) as session:
        row = session.get(ScheduledPost, post_id)
        if row:
            row.status = status
            row.content_json = json.dumps(content)
            session.commit()


def requeue_scheduled_post(user_id: int, post_id: int, now: datetime) -> bool:
    """A post that didn't (all) go out becomes due again, now."""
    with Session(engine) as session:
        changed = (
            session.query(ScheduledPost)
            .filter(ScheduledPost.id == post_id, ScheduledPost.user_id == user_id,
                    ScheduledPost.status.in_(("failed", "partial", "publishing")))
            .update({ScheduledPost.status: "pending", ScheduledPost.scheduled_at: now}, synchronize_session=False)
        )
        session.commit()
        return bool(changed)


def cancel_scheduled_post(user_id: int, post_id: int) -> bool:
    """Cancel a pending scheduled post."""
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        post = (
            session.query(ScheduledPost).filter(ScheduledPost.id == post_id, ScheduledPost.user_id == user_id).first()
        )

        if post and post.status == "pending":
            post.status = "cancelled"
            session.commit()
            return True
        return False


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def save_competitor_posts(posts: list[dict]) -> dict:
    """
    Persist scraped competitor posts, inserting only records not already
    stored (matched by competitor + platform + post_url). Existing posts
    are left untouched. Returns {"inserted": n, "skipped": n}.
    """
    if not posts:
        return {"inserted": 0, "skipped": 0}

    with Session(engine) as session:
        keys = {
            (p.get("_source_competitor") or "Unknown", (p.get("platform") or "").lower(), p.get("post_url"))
            for p in posts
            if p.get("post_url")
        }
        existing = set()
        if keys:
            competitors = {k[0] for k in keys}
            platforms = {k[1] for k in keys}
            rows = (
                session.query(CompetitorPost.competitor, CompetitorPost.platform, CompetitorPost.post_url)
                .filter(CompetitorPost.competitor.in_(competitors), CompetitorPost.platform.in_(platforms))
                .all()
            )
            existing = {(r[0], (r[1] or "").lower(), r[2]) for r in rows}

        inserted = 0
        skipped = 0
        new_post_urls = []
        newly_inserted_posts = []
        for p in posts:
            competitor = p.get("_source_competitor") or "Unknown"
            platform = (p.get("platform") or "").lower()
            post_url = p.get("post_url")
            if not post_url:
                skipped += 1
                continue

            key = (competitor, platform, post_url)
            if key in existing:
                skipped += 1
                continue

            session.add(
                CompetitorPost(
                    competitor=competitor,
                    platform=platform,
                    post_url=post_url,
                    title=p.get("title"),
                    text=p.get("text"),
                    author=p.get("author"),
                    post_type=p.get("post_type"),
                    engagement_json=json.dumps(p.get("engagement")) if p.get("engagement") is not None else None,
                    media_json=json.dumps(p.get("media")) if p.get("media") is not None else None,
                    published_at=_parse_dt(p.get("published_at")),
                    scraped_at=_parse_dt(p.get("scraped_at")),
                    raw_json=json.dumps(p, default=str),
                )
            )
            existing.add(key)
            new_post_urls.append(post_url)
            newly_inserted_posts.append(p)
            inserted += 1

        session.commit()

        # Embed newly-inserted posts now so Suggested Storyline clustering
        # (services/embedding_service.py) doesn't have to embed them on-demand
        # at suggestion-generation time. Best-effort - embedding is a similarity
        # feature, never allowed to break scraping/saving.
        if newly_inserted_posts:
            try:
                from services.embedding_service import EmbeddingService

                EmbeddingService().index_posts(newly_inserted_posts)
            except Exception as e:
                logger.warning(f"Embedding indexing warning: {e}")

        return {"inserted": inserted, "skipped": skipped, "new_post_urls": new_post_urls}


def get_competitor_posts(
    platform: str | None = None, competitor: str | None = None, limit: int = 300, days: int = 15
) -> list[dict]:
    """Return competitor posts from the last `days` days, stored in the DB, newest first."""
    with Session(engine) as session:
        query = session.query(CompetitorPost)
        if platform and platform.lower() != "all":
            query = query.filter(CompetitorPost.platform == platform.lower())
        if competitor and competitor.lower() != "all":
            query = query.filter(CompetitorPost.competitor == competitor)

        order_col = func.coalesce(CompetitorPost.published_at, CompetitorPost.scraped_at, CompetitorPost.created_at)
        if days:
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
            query = query.filter(order_col >= cutoff)
        rows = query.order_by(order_col.desc()).limit(limit).all()

        return [
            {
                "title": r.title,
                "text": r.text,
                "platform": r.platform,
                "post_url": r.post_url,
                "author": r.author,
                "post_type": r.post_type,
                "published_at": r.published_at.isoformat() if r.published_at else None,
                "scraped_at": r.scraped_at.isoformat() if r.scraped_at else None,
                "_source_competitor": r.competitor,
            }
            for r in rows
        ]


def save_opportunity_suggestions(
    unserved_themes: list[dict], domain_expansion: list[dict], source_accounts: str = ""
) -> dict:
    """
    Persist LLM-generated opportunity suggestions, inserting only titles not
    already stored per category (matched case-insensitively). Returns
    {"inserted": n, "skipped": n, "new_titles": [...]}.
    """
    items = [("unserved_theme", i) for i in (unserved_themes or [])] + [
        ("domain_expansion", i) for i in (domain_expansion or [])
    ]
    if not items:
        return {"inserted": 0, "skipped": 0, "new_titles": []}

    with Session(engine) as session:
        existing = {
            (r[0], r[1].strip().lower())
            for r in session.query(OpportunitySuggestion.category, OpportunitySuggestion.title).all()
        }

        inserted = 0
        skipped = 0
        new_titles = []
        for category, item in items:
            title = (item.get("title") or "").strip()
            if not title:
                skipped += 1
                continue

            key = (category, title.lower())
            if key in existing:
                skipped += 1
                continue

            session.add(
                OpportunitySuggestion(
                    category=category, title=title, description=item.get("description"), source_accounts=source_accounts
                )
            )
            existing.add(key)
            new_titles.append(title)
            inserted += 1

        session.commit()
        return {"inserted": inserted, "skipped": skipped, "new_titles": new_titles}


def get_opportunity_suggestions(limit: int = 200) -> dict:
    """Return all accumulated opportunity suggestions, grouped by category, newest first."""
    with Session(engine) as session:
        rows = session.query(OpportunitySuggestion).order_by(OpportunitySuggestion.created_at.desc()).limit(limit).all()

        result: dict[str, list] = {"unserved_themes": [], "domain_expansion": []}
        key_map = {"unserved_theme": "unserved_themes", "domain_expansion": "domain_expansion"}
        for r in rows:
            bucket = key_map.get(r.category)
            if not bucket:
                continue
            result[bucket].append(
                {
                    "title": r.title,
                    "description": r.description,
                    "source_accounts": r.source_accounts,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
            )
        return result


def _post_urls_hash(post_urls: list[str]) -> str:
    """Stable fingerprint for a cluster's exact post composition, used to
    dedupe re-generated Suggested Storylines that group the same posts."""
    joined = "|".join(sorted(u for u in post_urls if u))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def save_content_collections(collections: list[dict]) -> dict:
    """
    Persist Suggested Storyline collections, skipping any whose exact post
    composition (post_urls_hash) is already stored. Returns
    {"inserted": n, "skipped": n, "new_hashes": [...]}.
    """
    if not collections:
        return {"inserted": 0, "skipped": 0, "new_hashes": []}

    with Session(engine) as session:
        existing = {r[0] for r in session.query(ContentCollection.post_urls_hash).all()}

        inserted = 0
        skipped = 0
        new_hashes = []
        for item in collections:
            post_urls = item.get("post_urls") or []
            if len(post_urls) < 2:
                skipped += 1
                continue

            urls_hash = _post_urls_hash(post_urls)
            if urls_hash in existing:
                skipped += 1
                continue

            session.add(
                ContentCollection(
                    post_urls_hash=urls_hash,
                    label=item.get("label") or "Related Storyline",
                    description=item.get("description"),
                    relevance=item.get("relevance") or "medium",
                    competitors=", ".join(item.get("competitors") or []),
                    platforms=", ".join(item.get("platforms") or []),
                    post_urls=json.dumps(post_urls),
                    post_count=item.get("post_count") or len(post_urls),
                )
            )
            existing.add(urls_hash)
            new_hashes.append(urls_hash)
            inserted += 1

        session.commit()
        return {"inserted": inserted, "skipped": skipped, "new_hashes": new_hashes}


def delete_old_content_collections(days: int = 15) -> int:
    """Delete Suggested Storyline collections older than a specified number of days.
    Returns the number of rows deleted."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    with Session(engine) as session:
        deleted = session.query(ContentCollection).filter(ContentCollection.created_at < cutoff).delete()
        session.commit()
        return deleted


def get_content_collections(limit: int = 50) -> list[dict]:
    """Return all accumulated Suggested Storyline collections, newest first."""
    delete_old_content_collections(days=15)
    with Session(engine) as session:
        rows = session.query(ContentCollection).order_by(ContentCollection.created_at.desc()).limit(limit).all()

        return [
            {
                "post_urls_hash": r.post_urls_hash,
                "label": r.label,
                "description": r.description,
                "relevance": r.relevance,
                "competitors": [c.strip() for c in (r.competitors or "").split(",") if c.strip()],
                "platforms": [p.strip() for p in (r.platforms or "").split(",") if p.strip()],
                "post_urls": json.loads(r.post_urls) if r.post_urls else [],
                "post_count": r.post_count,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


def clear_content_collections() -> int:
    """Delete every stored Suggested Storyline collection. Used when
    regenerating the whole list from scratch instead of accumulating on top
    of a stale prior batch. Returns the number of rows deleted."""
    with Session(engine) as session:
        deleted = session.query(ContentCollection).delete()
        session.commit()
        return deleted


def post_urls_hash(post_urls: list[str]) -> str:
    """Public wrapper for the same stable fingerprint save_content_collections
    uses internally - lets callers (e.g. the repeat-filtering in
    generate_suggested_collections) compute a cluster's hash before deciding
    whether to persist/display it."""
    return _post_urls_hash(post_urls)


def get_seen_storyline_hashes() -> set[str]:
    """Every post_urls_hash ever surfaced as a Suggested Storyline (see
    SuggestedStorylineSeen) - persists across "regenerate" clears, unlike
    ContentCollection, so it can be used to filter out repeats."""
    with Session(engine) as session:
        rows = session.query(SuggestedStorylineSeen.post_urls_hash).all()
        return {r[0] for r in rows}


def mark_storylines_seen(hashes: list[str]) -> None:
    """Records newly-surfaced storyline hashes as seen, ignoring ones already
    recorded (a hash reappearing just means the same posts clustered again;
    no need to update first_seen_at)."""
    if not hashes:
        return
    with Session(engine) as session:
        existing = {
            r[0]
            for r in session.query(SuggestedStorylineSeen.post_urls_hash)
            .filter(SuggestedStorylineSeen.post_urls_hash.in_(hashes))
            .all()
        }
        for h in hashes:
            if h not in existing:
                session.add(SuggestedStorylineSeen(post_urls_hash=h))
                existing.add(h)  # guard against duplicate hashes within the same batch
        session.commit()


def get_setting(key: str, default: str = "") -> str:
    """Reads an editable app setting (see AppSetting). Returns `default` if
    never saved yet."""
    with Session(engine) as session:
        row = session.get(AppSetting, key)
        return row.value if row else default


def save_setting(key: str, value: str) -> dict:
    """Creates or updates an editable app setting."""
    with Session(engine) as session:
        row = session.get(AppSetting, key)
        if row:
            row.value = value  # type: ignore[assignment]
        else:
            row = AppSetting(key=key, value=value)
            session.add(row)
        session.commit()
        session.refresh(row)
        return {
            "key": row.key,
            "value": row.value,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


def _brand_asset_to_dict(r: "BrandAsset") -> dict:
    return {"key": r.key, "label": r.label, "filename": r.filename, "url": f"/static/img/brand/{r.filename}"}


def list_brand_assets() -> list[dict]:
    """All registered brand character/logo assets, oldest first - seeds the
    three legacy defaults (Aiden, Ida, StradIT Logo) on first call if the table is
    still empty, since those files already exist on disk from before this
    feature existed."""
    with Session(engine) as session:
        rows = session.query(BrandAsset).order_by(BrandAsset.created_at.asc()).all()
        if not rows:
            defaults = [
                BrandAsset(key="aiden", label="Aiden — Brand Mascot", filename="aiden-character.png"),
                BrandAsset(key="ida", label="Ida — Brand Mascot", filename="Ida.jpeg"),
                BrandAsset(key="logo", label="StradIT Logo", filename="stradit-logo.png"),
            ]
            session.add_all(defaults)
            session.commit()
            rows = defaults
        return [_brand_asset_to_dict(r) for r in rows]


def get_brand_asset(key: str) -> dict | None:
    with Session(engine) as session:
        row = session.get(BrandAsset, key)
        return _brand_asset_to_dict(row) if row else None


def create_brand_asset(key: str, label: str, filename: str) -> dict:
    with Session(engine) as session:
        row = BrandAsset(key=key, label=label, filename=filename)
        session.add(row)
        session.commit()
        session.refresh(row)
        return _brand_asset_to_dict(row)


def update_brand_asset_filename(key: str, filename: str) -> dict | None:
    """Used when replacing an existing asset's image (filename usually stays
    the same, but is updated here in case the new upload has a different
    extension)."""
    with Session(engine) as session:
        row = session.get(BrandAsset, key)
        if not row:
            return None
        row.filename = filename  # type: ignore[assignment]
        session.commit()
        session.refresh(row)
        return _brand_asset_to_dict(row)


def delete_brand_asset(key: str) -> bool:
    with Session(engine) as session:
        row = session.get(BrandAsset, key)
        if not row:
            return False
        session.delete(row)
        session.commit()
        return True


def _approval_request_to_dict(r: "ApprovalRequest") -> dict:
    return {
        "id": r.id,
        "user_id": r.user_id,
        "pipeline_client_id": r.pipeline_client_id,
        "platform": r.platform,
        "asset_type": r.asset_type,
        "caption": r.caption,
        "story_context": r.story_context,
        "competitors": [c.strip() for c in (r.competitors or "").split(",") if c.strip()],
        "image_urls": json.loads(r.image_urls) if r.image_urls else [],
        "status": r.status,
        "comments": r.comments,
        "decided_by": r.decided_by,
        "decided_at": _as_utc(r.decided_at).isoformat() if r.decided_at else None,
        "compliance": json.loads(r.compliance) if getattr(r, "compliance", None) else None,
        "reviewer_email": getattr(r, "reviewer_email", None),
        "items": json.loads(r.items) if getattr(r, "items", None) else None,
        "reminder_count": getattr(r, "reminder_count", None) or 0,
        "last_reminder_at": _as_utc(r.last_reminder_at).isoformat() if getattr(r, "last_reminder_at", None) else None,
        # UTC with its offset, so the browser shows it in the viewer's time zone
        "created_at": _as_utc(r.created_at).isoformat() if r.created_at else None,
    }


def create_approval_request(
    user_id: int | None,
    pipeline_client_id: str,
    platform: str,
    asset_type: str,
    caption: str | None = None,
    story_context: str | None = None,
    competitors: list[str] | None = None,
    image_urls: list[str] | None = None,
    compliance: dict | None = None,
    reviewer_email: str | None = None,
    items: list[dict] | None = None,
) -> dict:
    """Creates a new pending approval request for a pipeline's generated
    content, superseding the same user's earlier pending request for that
    pipeline (re-sending for approval after edits shouldn't leave stale
    duplicates) - never another user's. items: a whole multi-platform post
    (services/approval_bundle_service.py)."""
    with Session(engine) as session:
        session.query(ApprovalRequest).filter(
            ApprovalRequest.pipeline_client_id == pipeline_client_id,
            ApprovalRequest.status == "pending",
            ApprovalRequest.user_id == user_id,
        ).delete()

        req = ApprovalRequest(
            user_id=user_id,
            pipeline_client_id=pipeline_client_id,
            platform=platform,
            asset_type=asset_type,
            caption=caption,
            story_context=story_context,
            competitors=", ".join(competitors or []),
            image_urls=json.dumps(image_urls or []),
            compliance=json.dumps(compliance) if compliance else None,
            reviewer_email=(reviewer_email or "").strip().lower() or None,
            items=json.dumps(items) if items else None,
        )
        session.add(req)
        session.commit()
        session.refresh(req)
        return _approval_request_to_dict(req)


def get_approval_request(request_id: int) -> dict | None:
    with Session(engine) as session:
        req = session.get(ApprovalRequest, request_id)
        return _approval_request_to_dict(req) if req else None


def approvals_awaiting_reminder(now: datetime, after_hours: tuple, max_age_days: int, retry_hours: int = 12) -> list[dict]:
    """Pending requests due their next reminder: reminder n goes out once the
    request is after_hours[n] old. Older than max_age_days: left alone. One
    tried in the last retry_hours (sent or failed) waits."""
    with Session(engine) as session:
        rows = (
            session.query(ApprovalRequest)
            .filter(ApprovalRequest.status == "pending", ApprovalRequest.reviewer_email.isnot(None),
                    ApprovalRequest.created_at >= now - timedelta(days=max_age_days))
            .order_by(ApprovalRequest.created_at.asc())
            .all()
        )
        due = []
        for row in rows:
            sent = row.reminder_count or 0
            if sent >= len(after_hours) or _as_utc(row.created_at) > now - timedelta(hours=after_hours[sent]):
                continue
            if row.last_reminder_at and _as_utc(row.last_reminder_at) > now - timedelta(hours=retry_hours):
                continue
            due.append(_approval_request_to_dict(row))
        return due


def mark_approval_reminder(request_id: int, now: datetime, sent: bool) -> None:
    """Records a reminder attempt; only a sent one counts towards the total."""
    with Session(engine) as session:
        row = session.get(ApprovalRequest, request_id)
        if not row:
            return
        row.last_reminder_at = now
        if sent:
            row.reminder_count = (row.reminder_count or 0) + 1
        session.commit()


def _visible_to(query, user_id: int | None, email: str | None):
    """Restricts an ApprovalRequest query to requests the user owns or was
    sent as reviewer. user_id None = no restriction (admins)."""
    if user_id is None:
        return query
    condition = ApprovalRequest.user_id == user_id
    if email:
        condition = or_(condition, ApprovalRequest.reviewer_email == email.strip().lower())
    return query.filter(condition)


def get_latest_approval_request_for_pipeline(
    pipeline_client_id: str, user_id: int | None = None, email: str | None = None
) -> dict | None:
    """The most recent approval request for a pipeline (pending or decided) -
    used by the dashboard's "Approval" pipeline stage to show current status.
    user_id/email restrict it to requests that user owns or reviews."""
    with Session(engine) as session:
        query = session.query(ApprovalRequest).filter(ApprovalRequest.pipeline_client_id == pipeline_client_id)
        req = _visible_to(query, user_id, email).order_by(ApprovalRequest.created_at.desc()).first()
        return _approval_request_to_dict(req) if req else None


def list_approval_requests(
    status: str | None = None, limit: int = 200, user_id: int | None = None, email: str | None = None
) -> list[dict]:
    """Approval requests (past and current), newest first - powers the
    /approve dashboard. Optionally filtered to a single status; user_id/email
    restrict it to requests that user owns or was sent as reviewer."""
    with Session(engine) as session:
        query = _visible_to(session.query(ApprovalRequest), user_id, email)
        if status:
            query = query.filter(ApprovalRequest.status == status)
        rows = query.order_by(ApprovalRequest.created_at.desc()).limit(limit).all()
        return [_approval_request_to_dict(r) for r in rows]


def decide_approval_request(
    request_id: int, decision: str | None, comments: str | None, decided_by: str | None,
    item_decisions: dict | None = None,
) -> dict | None:
    """Records the reviewer's decision. decision 'approved'/'rejected' applies to
    the whole request (and every platform of a multi-platform one);
    item_decisions {index: {"decision": "approved"|"changes", "comment"}}
    decides a multi-platform request per platform - its status becomes
    approved, rejected (changes asked on every platform) or partial."""
    from services.approval_bundle_service import apply_decisions

    if decision not in ("approved", "rejected", None) or (decision is None and not item_decisions):
        raise ValueError("decision must be 'approved' or 'rejected'")

    with Session(engine) as session:
        req = session.get(ApprovalRequest, request_id)
        if not req:
            return None
        items = json.loads(req.items) if req.items else None
        if items:
            items, decision = apply_decisions(items, decision, item_decisions or {})
            req.items = json.dumps(items)  # type: ignore[assignment]
        elif decision is None:
            raise ValueError("decision must be 'approved' or 'rejected'")
        req.status = decision  # type: ignore[assignment]
        req.comments = comments  # type: ignore[assignment]
        req.decided_by = decided_by  # type: ignore[assignment]
        req.decided_at = _utcnow()  # type: ignore[assignment]
        session.commit()
        session.refresh(req)
        return _approval_request_to_dict(req)


def update_approval_item(request_id: int, index: int, fields: dict) -> dict | None:
    """Merges fields into one platform item of a multi-platform request (e.g. what publishing it did)."""
    with Session(engine) as session:
        req = session.get(ApprovalRequest, request_id)
        if not req or not req.items:
            return None
        items = json.loads(req.items)
        if not 0 <= index < len(items):
            return None
        items[index] = {**items[index], **fields}
        req.items = json.dumps(items)  # type: ignore[assignment]
        session.commit()
        session.refresh(req)
        return _approval_request_to_dict(req)


def update_scheduled_post_status(user_id: int, post_id: int, status: str) -> bool:
    """Update status of a scheduled post."""
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        post = (
            session.query(ScheduledPost).filter(ScheduledPost.id == post_id, ScheduledPost.user_id == user_id).first()
        )

        if post:
            post.status = status
            session.commit()
            return True
        return False


# ── Email history + email automation (Admin -> Emails) ───────────────────────

EMAIL_KINDS = {
    "weekly_ideas": "Weekly ideas", "nudge": "Festival / trend nudge", "festival_idea": "Festival idea (admin)",
    "ideas_campaign": "Ideas (admin)", "monthly_recap": "Monthly recap", "verification": "Email verification",
    "password_reset": "Password reset", "invitation": "Invitation", "approval_request": "Approval request",
    "approval_notification": "Approval notification", "approval_decision": "Approval decision",
    "credit_decision": "Credit decision", "sales_lead": "Sales lead", "approval_reminder": "Approval reminder",
}


def log_email(to_email: str, kind: str, subject: str, status: str, error: str | None = None,
              details: dict | None = None, triggered_by: str | None = None, user_id: int | None = None) -> None:
    """Records one send attempt. Never raises - logging must not break an email."""
    try:
        with Session(engine) as session:
            if user_id is None and to_email:
                user = session.query(User.id).filter(func.lower(User.email) == to_email.strip().lower()).first()
                user_id = user[0] if user else None
            session.add(EmailLog(
                user_id=user_id, to_email=(to_email or "")[:320], kind=(kind or "other")[:32],
                subject=(subject or "")[:300], status=status[:12], error=error[:500] if error else None,
                details=json.dumps(details, default=str)[:4000] if details else None,
                triggered_by=(triggered_by or "system")[:40],
            ))
            session.commit()
    except Exception as err:  # noqa: BLE001
        logger.warning(f"Could not log email to {to_email}: {err}")


def _email_row(row: "EmailLog", names: dict) -> dict:
    try:
        details = json.loads(row.details) if row.details else {}
    except ValueError:
        details = {}
    return {
        "id": row.id, "user_id": row.user_id, "user_name": names.get(row.user_id), "to_email": row.to_email,
        "kind": row.kind, "kind_label": EMAIL_KINDS.get(row.kind, row.kind.replace("_", " ").capitalize()),
        "subject": row.subject, "status": row.status, "error": row.error, "details": details,
        "triggered_by": row.triggered_by, "created_at": _as_utc(row.created_at).isoformat(),
    }


def list_email_log(page: int = 1, page_size: int = 25, kind: str | None = None, status: str | None = None,
                   q: str | None = None, date_from: datetime | None = None, date_to: datetime | None = None) -> dict:
    """Newest first, with filters. date_to is exclusive."""
    page_size = max(5, min(int(page_size or 25), 100))
    with Session(engine) as session:
        query = session.query(EmailLog)
        if kind:
            query = query.filter(EmailLog.kind == kind)
        if status:
            query = query.filter(EmailLog.status == status)
        if q:
            like = f"%{q.strip().lower()}%"
            query = query.filter(func.lower(EmailLog.to_email).like(like) | func.lower(EmailLog.subject).like(like))
        if date_from:
            query = query.filter(EmailLog.created_at >= date_from)
        if date_to:
            query = query.filter(EmailLog.created_at < date_to)
        total = query.count()
        pages = max(1, -(-total // page_size))
        page = max(1, min(int(page or 1), pages))
        rows = (query.order_by(EmailLog.created_at.desc(), EmailLog.id.desc())
                .offset((page - 1) * page_size).limit(page_size).all())
        ids = {r.user_id for r in rows if r.user_id}
        names = dict(session.query(User.id, User.name).filter(User.id.in_(ids)).all()) if ids else {}
        return {"emails": [_email_row(r, names) for r in rows], "total": total, "page": page,
                "pages": pages, "page_size": page_size}


def email_log_summary(days: int = 7) -> dict:
    """Totals for the last `days` days: sent, failed and sent per kind."""
    since = (_utcnow() - timedelta(days=days)).replace(tzinfo=None)
    with Session(engine) as session:
        rows = (session.query(EmailLog.kind, EmailLog.status, func.count(EmailLog.id))
                .filter(EmailLog.created_at >= since).group_by(EmailLog.kind, EmailLog.status).all())
    summary = {"days": days, "sent": 0, "failed": 0, "by_kind": {}}
    for kind, status, count in rows:
        summary["sent" if status == "sent" else "failed"] += count
        if status == "sent":
            summary["by_kind"][kind] = summary["by_kind"].get(kind, 0) + count
    return summary


def emailed_recently(user_id: int, kinds: tuple[str, ...], hours: int = 24) -> bool:
    """Whether the user was successfully sent one of `kinds` in the last `hours`."""
    since = (_utcnow() - timedelta(hours=hours)).replace(tzinfo=None)
    with Session(engine) as session:
        return session.query(EmailLog.id).filter(
            EmailLog.user_id == user_id, EmailLog.kind.in_(kinds), EmailLog.status == "sent",
            EmailLog.created_at >= since,
        ).first() is not None


# Scheduled emails an admin can switch on/off without a redeploy. The .env
# values (WEEKLY_IDEAS_EMAIL, NUDGE_EMAILS, MONTHLY_RECAP_EMAIL) are the
# defaults until an admin changes a switch.
EMAIL_AUTOMATIONS = {"weekly_ideas": "WEEKLY_IDEAS_EMAIL", "nudge_emails": "NUDGE_EMAILS",
                     "monthly_recap": "MONTHLY_RECAP_EMAIL", "approval_reminders": "APPROVAL_REMINDER_EMAILS"}


def get_email_automation() -> dict:
    """{name: {"enabled", "source"}}; source is "admin" (switched in Admin) or "env"."""
    from config import Config

    try:
        saved = json.loads(get_setting("email_automation", "") or "{}")
    except Exception:  # noqa: BLE001 - no database / bad value: the .env defaults
        saved = {}
    return {
        name: {"enabled": saved[name], "source": "admin"} if isinstance(saved.get(name), bool)
        else {"enabled": bool(getattr(Config, env_name, False)), "source": "env"}
        for name, env_name in EMAIL_AUTOMATIONS.items()
    }


def email_automation_enabled(name: str) -> bool:
    return get_email_automation()[name]["enabled"]


def set_email_automation(changes: dict) -> dict:
    try:
        saved = json.loads(get_setting("email_automation", "") or "{}")
    except ValueError:
        saved = {}
    for name, value in changes.items():
        if name in EMAIL_AUTOMATIONS and isinstance(value, bool):
            saved[name] = value
    save_setting("email_automation", json.dumps(saved))
    return get_email_automation()


def users_in_segment(industry_category: str | None = None, account_type: str | None = None,
                     region: str | None = None) -> list[dict]:
    """Active, verified users with a brand profile matching the filters (None = any),
    each with the opt-outs an email type has to respect."""
    with Session(engine) as session:
        rows = (session.query(User, UserBrandProfile)
                .join(UserBrandProfile, UserBrandProfile.user_id == User.id)
                .filter(User.is_active.isnot(False), User.email_verified.is_(True)).all())
        users = []
        for user, profile in rows:
            regions = []
            for raw in (profile.compliance_regions, getattr(profile, "regions_detected", None)):
                try:
                    regions = json.loads(raw) if raw else []
                except (ValueError, TypeError):
                    regions = []
                if regions:
                    break
            category = profile.industry_category or getattr(profile, "industry_category_detected", None) or "general"
            if industry_category and category != industry_category:
                continue
            if account_type and (user.account_type or "") != account_type:
                continue
            if region and region not in regions:
                continue
            users.append({
                "id": user.id, "name": user.name, "email": user.email, "account_type": user.account_type,
                "industry_category": category, "regions": regions, "company_name": profile.company_name,
                "ideas_ok": user.ideas_email_enabled is not False, "nudges_ok": user.nudge_email_enabled is not False,
            })
    return users


def occasion_emailed(user_id: int, occasion_name: str) -> bool:
    """Whether the user was already emailed about this occasion (nudge or admin send)."""
    needle = json.dumps(occasion_name)  # matches '"occasion": "Diwali"' in the JSON details
    with Session(engine) as session:
        rows = session.query(EmailLog.details).filter(
            EmailLog.user_id == user_id, EmailLog.status == "sent", EmailLog.kind.in_(("nudge", "festival_idea")),
            EmailLog.details.like(f"%{needle}%"),
        ).all()
    for (details,) in rows:
        try:
            if json.loads(details or "{}").get("occasion") == occasion_name:
                return True
        except ValueError:
            continue
    return False


# ── In-app guide (getting-started checklist + tours) ─────────────────────────


def get_guide_state(user_id: int) -> dict:
    with Session(engine) as session:
        user = session.get(User, user_id)
        try:
            state = json.loads(user.guide_state) if user and user.guide_state else {}
        except ValueError:
            state = {}
    return {"tours": list(state.get("tours") or []), "checklist_dismissed": bool(state.get("checklist_dismissed")),
            "events": list(state.get("events") or [])}


def save_guide_state(user_id: int, state: dict) -> dict:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user:
            user.guide_state = json.dumps(state)
            session.commit()
    return state


def guide_progress_facts(user_id: int) -> dict:
    """What the user has done so far, for the getting-started checklist - one cheap query each."""
    with Session(engine) as session:
        def exists(query) -> bool:
            return session.query(query.exists()).scalar()

        profile = session.query(UserBrandProfile).filter(UserBrandProfile.user_id == user_id).first()
        return {
            "brand_profile": bool(profile and (profile.company_name or profile.website)),
            "post": exists(session.query(RunHistory.id).filter(RunHistory.user_id == user_id)),
            "image": exists(session.query(ImageGeneration.id).filter(ImageGeneration.user_id == user_id)),
            "idea_link": exists(session.query(IdeaLink.token).filter(IdeaLink.user_id == user_id, IdeaLink.opened_at.isnot(None))),
            "approval": exists(session.query(ApprovalRequest.id).filter(ApprovalRequest.user_id == user_id)),
            "social": exists(session.query(SocialAccount.id).filter(SocialAccount.user_id == user_id,
                                                                     SocialAccount.status == "connected")),
            "published": exists(session.query(ScheduledPost.id).filter(ScheduledPost.user_id == user_id))
            or exists(session.query(ApprovalRequest.id).filter(ApprovalRequest.user_id == user_id,
                                                               ApprovalRequest.items.like('%"published": {%'))),
        }



# ── Autopilot (services/autopilot_service.py) ─────────────────────────────
AUTOPILOT_LIST_FIELDS = ("platforms", "topics", "avoid", "days_off")


def _autopilot_settings_dict(row: "AutopilotSettings | None", user_id: int) -> dict:
    if row is None:
        return {"user_id": user_id, "configured": False, "enabled": False, "paused": False, "posts_per_week": 3,
                "platforms": [], "mix": {}, "festivals": True, "topics": [], "avoid": [], "days_off": [6],
                "images": True, "image_cap_month": 12, "language": "en", "tz_minutes": 0}
    out = {"user_id": user_id, "configured": True, "enabled": bool(row.enabled), "paused": bool(row.paused),
           "posts_per_week": int(row.posts_per_week), "festivals": bool(row.festivals), "images": bool(row.images),
           "image_cap_month": int(row.image_cap_month), "language": row.language or "en", "tz_minutes": int(row.tz_minutes or 0)}
    for field in AUTOPILOT_LIST_FIELDS:
        try:
            out[field] = list(json.loads(getattr(row, field) or "[]"))
        except ValueError:
            out[field] = []
    try:
        out["mix"] = dict(json.loads(row.mix or "{}"))
    except ValueError:
        out["mix"] = {}
    return out


def get_autopilot_settings(user_id: int) -> dict:
    with Session(engine) as session:
        return _autopilot_settings_dict(session.get(AutopilotSettings, user_id), user_id)


def save_autopilot_settings(user_id: int, **fields) -> dict:
    with Session(engine) as session:
        row = session.get(AutopilotSettings, user_id)
        if row is None:
            row = AutopilotSettings(user_id=user_id)
            session.add(row)
        for key, value in fields.items():
            if key in AUTOPILOT_LIST_FIELDS or key == "mix":
                value = json.dumps(value)
            setattr(row, key, value)
        row.updated_at = _utcnow()
        session.commit()
        return _autopilot_settings_dict(row, user_id)


def autopilot_user_ids() -> list[int]:
    """Users whose Autopilot is on and not paused."""
    with Session(engine) as session:
        return [r[0] for r in session.query(AutopilotSettings.user_id)
                .filter(AutopilotSettings.enabled.is_(True), AutopilotSettings.paused.is_(False)).all()]


def _autopilot_post_dict(row: "AutopilotPost") -> dict:
    def _list(text):
        try:
            return list(json.loads(text or "[]"))
        except ValueError:
            return []
    return {"id": row.id, "user_id": row.user_id, "slot_at": row.slot_at.replace(tzinfo=timezone.utc).isoformat(),
            "platform": row.platform, "kind": row.kind, "reason": row.reason, "topic": row.topic, "status": row.status,
            "caption": row.caption, "hashtags": _list(row.hashtags), "image_url": row.image_url, "checks": _list(row.checks),
            "approve_by": row.approve_by.replace(tzinfo=timezone.utc).isoformat() if row.approve_by else None,
            "scheduled_post_id": row.scheduled_post_id, "cost_usd": float(row.cost_usd or 0.0), "error": row.error}


def list_autopilot_posts(user_id: int, start: datetime, end: datetime) -> list[dict]:
    """Posts whose slot is in [start, end) (aware or naive UTC), soonest first."""
    start = start.astimezone(timezone.utc).replace(tzinfo=None) if start.tzinfo else start
    end = end.astimezone(timezone.utc).replace(tzinfo=None) if end.tzinfo else end
    with Session(engine) as session:
        rows = (session.query(AutopilotPost)
                .filter(AutopilotPost.user_id == user_id, AutopilotPost.slot_at >= start, AutopilotPost.slot_at < end)
                .order_by(AutopilotPost.slot_at.asc(), AutopilotPost.id.asc()).all())
        return [_autopilot_post_dict(r) for r in rows]


def autopilot_posts_with_status(statuses: tuple[str, ...], user_id: int | None = None) -> list[dict]:
    with Session(engine) as session:
        query = session.query(AutopilotPost).filter(AutopilotPost.status.in_(statuses))
        if user_id is not None:
            query = query.filter(AutopilotPost.user_id == user_id)
        return [_autopilot_post_dict(r) for r in query.order_by(AutopilotPost.slot_at.asc()).all()]


def get_autopilot_post(user_id: int, post_id: int) -> dict | None:
    with Session(engine) as session:
        row = session.get(AutopilotPost, post_id)
        return _autopilot_post_dict(row) if row and row.user_id == user_id else None


def create_autopilot_post(user_id: int, slot_at: datetime, platform: str, kind: str, reason: str | None = None,
                          topic: str | None = None) -> dict | None:
    """None when that slot already has a post for the platform."""
    slot = slot_at.astimezone(timezone.utc).replace(tzinfo=None) if slot_at.tzinfo else slot_at
    with Session(engine) as session:
        if session.query(AutopilotPost.id).filter(AutopilotPost.user_id == user_id, AutopilotPost.slot_at == slot,
                                                  AutopilotPost.platform == platform).first():
            return None
        row = AutopilotPost(user_id=user_id, slot_at=slot, platform=platform, kind=kind,
                            reason=(reason or "")[:300] or None, topic=(topic or "")[:300] or None)
        session.add(row)
        session.commit()
        return _autopilot_post_dict(row)


def update_autopilot_post(post_id: int, **fields) -> dict | None:
    with Session(engine) as session:
        row = session.get(AutopilotPost, post_id)
        if row is None:
            return None
        for key, value in fields.items():
            if key in ("hashtags", "checks"):
                value = json.dumps(value or [])
            elif isinstance(value, datetime) and value.tzinfo:
                value = value.astimezone(timezone.utc).replace(tzinfo=None)
            setattr(row, key, value)
        row.updated_at = _utcnow()
        session.commit()
        return _autopilot_post_dict(row)


def delete_future_autopilot_posts(user_id: int, statuses: tuple[str, ...] = ("planned",)) -> int:
    """Removes not-yet-written slots (after the plan's settings change)."""
    with Session(engine) as session:
        count = (session.query(AutopilotPost)
                 .filter(AutopilotPost.user_id == user_id, AutopilotPost.status.in_(statuses),
                         AutopilotPost.slot_at > _utcnow()).delete(synchronize_session=False))
        session.commit()
        return int(count or 0)


def autopilot_images_since(user_id: int, since: datetime) -> int:
    since = since.astimezone(timezone.utc).replace(tzinfo=None) if since.tzinfo else since
    with Session(engine) as session:
        return int(session.query(func.count(AutopilotPost.id))
                   .filter(AutopilotPost.user_id == user_id, AutopilotPost.image_url.isnot(None),
                           AutopilotPost.created_at >= since).scalar() or 0)
