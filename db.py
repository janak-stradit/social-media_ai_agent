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

engine = create_engine(
    DATABASE_URL,
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
    credit_limit: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
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
    # Posts the user aims to create each week (Content Calendar). None = WEEKLY_POST_GOAL_DEFAULT.
    weekly_post_goal: Mapped[int | None] = mapped_column(Integer, nullable=True)


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
    status: Mapped[str] = mapped_column(
        String(32), default="connected", nullable=False
    )  # connected, disconnected, expired
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


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
    )  # pending, published, failed, cancelled
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class MemoryEmbedding(Base):
    __tablename__ = "memory_embeddings"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=True)
    embedding_array: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


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

    with engine.connect() as conn:
        run_tbl = f'"{SCHEMA}".run_history' if not IS_SQLITE else "run_history"
        img_tbl = f'"{SCHEMA}".image_generations' if not IS_SQLITE else "image_generations"
        usr_tbl = f'"{SCHEMA}".users' if not IS_SQLITE else "users"

        for alter_cmd in [
            f"ALTER TABLE {run_tbl} ADD COLUMN user_id INTEGER",
            f"ALTER TABLE {run_tbl} ADD COLUMN tokens_used INTEGER DEFAULT 0",
            f"ALTER TABLE {run_tbl} ADD COLUMN cost_usd DOUBLE PRECISION DEFAULT 0.0",
            f"ALTER TABLE {run_tbl} ADD COLUMN is_archived BOOLEAN DEFAULT FALSE",
            f"ALTER TABLE {run_tbl} ADD COLUMN conversation_id INTEGER",
            f"CREATE INDEX IF NOT EXISTS ix_run_history_conversation_id ON {run_tbl} (conversation_id)",
            f"ALTER TABLE {img_tbl} ADD COLUMN conversation_id INTEGER",
            f"ALTER TABLE {img_tbl} ADD COLUMN parent_id INTEGER",
            f"ALTER TABLE {img_tbl} ADD COLUMN root_id INTEGER",
            f"ALTER TABLE {img_tbl} ADD COLUMN version INTEGER",
            f"ALTER TABLE {img_tbl} ADD COLUMN prompt TEXT",
            f"ALTER TABLE {img_tbl} ADD COLUMN edit_instruction VARCHAR(1000)",
            f"ALTER TABLE {img_tbl} ADD COLUMN clean_url VARCHAR(1000)",
            f"ALTER TABLE {img_tbl} ADD COLUMN width INTEGER",
            f"ALTER TABLE {img_tbl} ADD COLUMN height INTEGER",
            f"CREATE INDEX IF NOT EXISTS ix_image_generations_conversation_id ON {img_tbl} (conversation_id)",
            f"CREATE INDEX IF NOT EXISTS ix_image_generations_parent_id ON {img_tbl} (parent_id)",
            f"CREATE INDEX IF NOT EXISTS ix_image_generations_root_id ON {img_tbl} (root_id)",
            f"ALTER TABLE {usr_tbl} ADD COLUMN credit_limit DOUBLE PRECISION DEFAULT 10.0",
            f"ALTER TABLE {usr_tbl} ADD COLUMN is_admin BOOLEAN DEFAULT FALSE",
            f"ALTER TABLE {usr_tbl} ADD COLUMN email_verified BOOLEAN DEFAULT FALSE",
            f"ALTER TABLE {usr_tbl} ADD COLUMN verification_token VARCHAR(64)",
            f"ALTER TABLE {usr_tbl} ADD COLUMN verification_sent_at TIMESTAMP",
            f"ALTER TABLE {usr_tbl} ADD COLUMN account_type VARCHAR(32)",
            f"ALTER TABLE {usr_tbl} ADD COLUMN company_website VARCHAR(500)",
            f"ALTER TABLE {usr_tbl} ADD COLUMN onboarding_completed BOOLEAN DEFAULT FALSE",
            f"ALTER TABLE {usr_tbl} ADD COLUMN is_active BOOLEAN DEFAULT TRUE",
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
            f"ALTER TABLE {usr_tbl} ADD COLUMN recap_email_enabled BOOLEAN",
            f"ALTER TABLE {usr_tbl} ADD COLUMN recap_email_last_month VARCHAR(7)",
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
            f"ALTER TABLE {approval_tbl} ADD COLUMN compliance TEXT",
            f"ALTER TABLE {approval_tbl} ADD COLUMN reviewer_email VARCHAR(255)",
            f"ALTER TABLE {approval_tbl} ADD COLUMN items TEXT",
            f"ALTER TABLE {invite_tbl} ADD COLUMN inviter_name VARCHAR(255)",
        ]:
            try:
                with engine.begin() as sub_conn:
                    sub_conn.execute(text(alter_cmd))
            except Exception:
                pass

        soc_tbl = f'"{SCHEMA}".social_accounts' if not IS_SQLITE else "social_accounts"
        for col_name, col_type in [
            ("connection_type", "VARCHAR(32) DEFAULT 'direct'"),
            ("mcp_endpoint", "TEXT"),
            ("mcp_token", "TEXT"),
            ("mcp_tool_name", "VARCHAR(120) DEFAULT 'linkedin_publish_post'"),
            ("refresh_token", "TEXT"),
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
                    existing.credit_limit = max(existing.credit_limit or 10.0, 1000.0)  # type: ignore[assignment]
                else:
                    new_admin = User(
                        name="System Admin",
                        email="admin@vortexsocial.ai",
                        password_hash=generate_password_hash("admin123"),
                        credit_limit=1000.0,
                        is_admin=True,
                    )
                    session.add(new_admin)
                session.commit()
                logger.warning("Default admin user created with the built-in password - change it now.")
    except Exception as seed_err:
        logger.warning(f"Warning seeding admin user: {seed_err}")

    try:
        made = _backfill_conversations()
        if made:
            logger.info(f"Grouped {made} earlier run(s) into conversations.")
    except Exception as conv_err:  # noqa: BLE001 - start-up must not fail on it
        logger.warning(f"Warning backfilling conversations: {conv_err}")


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


def create_user(name: str, email: str, password_hash: str, is_admin: bool = False, credit_limit: float = 10.0) -> dict:
    with Session(engine) as session:
        row = User(
            name=name.strip(),
            email=email.strip().lower(),
            password_hash=password_hash,
            is_admin=is_admin,
            credit_limit=credit_limit,
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
    lo = start.astimezone(timezone.utc).replace(tzinfo=None)
    hi = end.astimezone(timezone.utc).replace(tzinfo=None)
    with Session(engine) as session:
        rows = (
            session.query(ScheduledPost)
            .filter(ScheduledPost.user_id == user_id, ScheduledPost.scheduled_at >= lo, ScheduledPost.scheduled_at < hi,
                    ScheduledPost.status.in_(("pending", "published")))
            .order_by(ScheduledPost.scheduled_at.asc())
            .all()
        )
        out = []
        for row in rows:
            try:
                platforms = json.loads(row.platforms)
            except (TypeError, ValueError):
                platforms = [p.strip() for p in (row.platforms or "").split(",") if p.strip()]
            out.append({"id": row.id, "scheduled_at": _as_utc(row.scheduled_at), "status": row.status,
                        "platforms": platforms if isinstance(platforms, list) else []})
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
        "is_archived": bool(conv.is_archived),
        "active_image_id": conv.active_image_id,
    }


def list_conversations(user_id: int, limit: int = 30, archived: bool = False) -> list[dict]:
    with Session(engine) as session:
        convs = (
            session.query(Conversation)
            .filter(Conversation.user_id == user_id, Conversation.is_archived.is_(bool(archived)))
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
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
        session.commit()
        return int(row.id)


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
        raw_limit = user.image_limit
        if raw_limit is None:
            raw_limit = IMAGE_UNLIMITED if user.is_admin else settings["default_limit"]
        unlimited = raw_limit == IMAGE_UNLIMITED
        model = _user_image_model(user, settings)
        resets_at = day_start + timedelta(days=1)
        return {
            "used": int(used),
            "limit": None if unlimited else int(raw_limit),
            "unlimited": unlimited,
            "remaining": None if unlimited else max(0, int(raw_limit) - int(used)),
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
        limit = default_limit if user else 0
        resets_at = day_start + timedelta(days=1)
        return {
            # False = video is still "coming soon" for this user
            "enabled": bool(user and (user.is_admin or user.video_enabled)),
            "used": int(used),
            "limit": None if unlimited else int(limit),
            "unlimited": unlimited,
            "remaining": None if unlimited else max(0, int(limit) - int(used)),
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


INVITATION_TTL = timedelta(days=7)


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


def list_invitations(limit: int = 100) -> list[dict]:
    with Session(engine) as session:
        rows = session.query(UserInvitation).order_by(UserInvitation.sent_at.desc()).limit(limit).all()
        names = {u.id: u.name for u in session.query(User.id, User.name).all()}
        return [_serialize_invitation(r, names.get(r.invited_by_user_id)) for r in rows]


def get_invitation(invitation_id: int) -> dict | None:
    with Session(engine) as session:
        row = session.get(UserInvitation, invitation_id)
        if not row:
            return None
        inviter = session.get(User, row.invited_by_user_id) if row.invited_by_user_id else None
        return _serialize_invitation(row, inviter.name if inviter else None)


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


def get_user_usage_stats(user_id: int) -> dict:
    """Return aggregated token, cost metrics, and credit details for a user."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        credit_limit = user.credit_limit if user and user.credit_limit is not None else 10.0
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

        return {
            "total_runs": total_runs,
            "total_tokens": total_tokens,
            "total_cost_usd": round(used_cost, 6),
            "used_credits": round(used_cost, 4),
            "credit_limit": round(credit_limit, 2),
            "remaining_credits": round(remaining, 4),
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
        user.credit_limit = (user.credit_limit or 10.0) + req.requested_amount
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
            "credit_limit": round(user.credit_limit or 10.0, 2) if user else None,
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
            user.credit_limit = max(0.0, (user.credit_limit or 10.0) + add_amount)

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
            limit = u.credit_limit if u.credit_limit is not None else 10.0
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
) -> dict:
    """Create or update a connected social media account with optional MCP support."""
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
                status="connected",
            )
            session.add(acc)
        else:
            acc.account_name = account_name
            if account_id:
                acc.account_id = account_id
            if access_token:
                acc.access_token = access_token
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


def disconnect_social_account(user_id: int, platform: str) -> bool:
    """Set social account status to disconnected."""
    with Session(engine) as session:
        acc = (
            session.query(SocialAccount)
            .filter(SocialAccount.user_id == user_id, SocialAccount.platform == platform)
            .first()
        )
        if acc:
            acc.status = "disconnected"
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
            "status": post.status,
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

        results = []
        for p in posts:
            try:
                platforms = json.loads(p.platforms)
            except Exception:
                platforms = [p.platforms]
            try:
                content = json.loads(p.content_json)
            except Exception:
                content = {}

            results.append(
                {
                    "id": p.id,
                    "run_id": p.run_id,
                    "platforms": platforms,
                    "scheduled_at": p.scheduled_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": p.status,
                    "content_json": content,
                    "story": content.get("story") or content.get("caption") or "Campaign Post",
                    "created_at": p.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        return results


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
    "credit_decision": "Credit decision", "sales_lead": "Sales lead",
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
                     "monthly_recap": "MONTHLY_RECAP_EMAIL"}


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
