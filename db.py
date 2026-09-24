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
import os
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
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.sql import func


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
        usr_tbl = f'"{SCHEMA}".users' if not IS_SQLITE else "users"

        for alter_cmd in [
            f"ALTER TABLE {run_tbl} ADD COLUMN user_id INTEGER",
            f"ALTER TABLE {run_tbl} ADD COLUMN tokens_used INTEGER DEFAULT 0",
            f"ALTER TABLE {run_tbl} ADD COLUMN cost_usd DOUBLE PRECISION DEFAULT 0.0",
            f"ALTER TABLE {run_tbl} ADD COLUMN is_archived BOOLEAN DEFAULT FALSE",
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
        ]:
            try:
                with engine.begin() as sub_conn:
                    sub_conn.execute(text(alter_cmd))
            except Exception:
                pass

        profile_tbl = f'"{SCHEMA}".user_brand_profiles' if not IS_SQLITE else "user_brand_profiles"
        for alter_cmd in [
            f"ALTER TABLE {profile_tbl} ADD COLUMN company_name VARCHAR(255)",
            f"ALTER TABLE {profile_tbl} ADD COLUMN suggested_post_ideas TEXT",
            f"ALTER TABLE {profile_tbl} ADD COLUMN tagline VARCHAR(255)",
            f"ALTER TABLE {profile_tbl} ADD COLUMN visual_style TEXT",
            f"ALTER TABLE {profile_tbl} ADD COLUMN fonts TEXT",
            f"ALTER TABLE {profile_tbl} ADD COLUMN logo_url VARCHAR(1000)",
            f"ALTER TABLE {profile_tbl} ADD COLUMN core_products TEXT",
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
                print("[DB] Default Admin user (admin@vortexsocial.ai / admin123) initialized.")
    except Exception as seed_err:
        print(f"[DB] Warning seeding admin user: {seed_err}")


def _serialize_run(row: RunHistory, truncate_story: bool = False) -> dict:
    story = row.story if row.story else ""
    if truncate_story and len(story) > 120:
        story = story[:120] + "..."
    return {
        "id": row.id,
        "timestamp": row.created_at.strftime("%Y-%m-%d %H:%M" if truncate_story else "%Y-%m-%d %H:%M:%S"),
        "story": story,
        "tone": row.tone,
        "platforms": json.loads(row.platforms) if row.platforms else [],
        "content": json.loads(row.content) if row.content else {},
        "tokens_used": row.tokens_used or 0,
        "cost_usd": round(float(row.cost_usd or 0.0), 6),  # type: ignore
        "is_archived": bool(getattr(row, "is_archived", False)),
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
    SocialAccount,
    ApprovedAsset,
    ApprovalRequest,
    ScheduledPost,
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
) -> dict:
    """Upsert - see agents/website_analysis_agent.py for how these fields are
    derived. One row per user (unique on user_id)."""
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
        row.analyzed_at = _utcnow()
        session.commit()
        session.refresh(row)
        return {"id": row.id, "user_id": row.user_id, "website": row.website}


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
            "analyzed_at": row.analyzed_at.strftime("%Y-%m-%d %H:%M:%S"),
        }


def save_run(
    story: str, tone: str, platforms: list, content: dict, user_id: int, tokens_used: int = 0, cost_usd: float = 0.0
) -> int:
    with Session(engine) as session:
        row = RunHistory(
            user_id=user_id,
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
        session.commit()
        return {"request_id": req.id, "status": "rejected"}


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


def get_all_users_credit_summary() -> list[dict]:
    """Return credit summaries for all registered users (for admin management)."""
    with Session(engine) as session:
        users = session.query(User).order_by(User.created_at.asc()).all()
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
                }
            )
        return summaries


def get_global_cost_history(limit: int = 100) -> list[dict]:
    """Return all cost history runs across all users with user info (for admin)."""
    with Session(engine) as session:
        rows = (
            session.query(RunHistory, User)
            .outerjoin(User, RunHistory.user_id == User.id)
            .order_by(RunHistory.created_at.desc())
            .limit(limit)
            .all()
        )

        history = []
        for run, user in rows:
            story_snippet = run.story[:100] + "..." if len(run.story) > 100 else run.story
            history.append(
                {
                    "id": run.id,
                    "user_id": run.user_id,
                    "user_name": user.name if user else "Unknown",
                    "user_email": user.email if user else "N/A",
                    "timestamp": run.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "story": story_snippet,
                    "tone": run.tone,
                    "platforms": json.loads(run.platforms) if run.platforms else [],
                    "tokens_used": run.tokens_used or 0,
                    "cost_usd": round(run.cost_usd or 0.0, 6),
                }
            )
        return history


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
                print(f"[save_competitor_posts] Embedding indexing warning: {e}")

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
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
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
) -> dict:
    """Creates a new pending approval request for a pipeline's generated
    content, superseding any earlier pending request for the same pipeline
    (re-sending for approval after edits shouldn't leave stale duplicates)."""
    with Session(engine) as session:
        session.query(ApprovalRequest).filter(
            ApprovalRequest.pipeline_client_id == pipeline_client_id,
            ApprovalRequest.status == "pending",
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
        )
        session.add(req)
        session.commit()
        session.refresh(req)
        return _approval_request_to_dict(req)


def get_approval_request(request_id: int) -> dict | None:
    with Session(engine) as session:
        req = session.get(ApprovalRequest, request_id)
        return _approval_request_to_dict(req) if req else None


def get_latest_approval_request_for_pipeline(pipeline_client_id: str) -> dict | None:
    """The most recent approval request for a pipeline (pending or decided) -
    used by the dashboard's "Approval" pipeline stage to show current status."""
    with Session(engine) as session:
        req = (
            session.query(ApprovalRequest)
            .filter(ApprovalRequest.pipeline_client_id == pipeline_client_id)
            .order_by(ApprovalRequest.created_at.desc())
            .first()
        )
        return _approval_request_to_dict(req) if req else None


def list_approval_requests(status: str | None = None, limit: int = 200) -> list[dict]:
    """All approval requests (past and current), newest first - powers the
    /approve dashboard. Optionally filtered to a single status."""
    with Session(engine) as session:
        query = session.query(ApprovalRequest)
        if status:
            query = query.filter(ApprovalRequest.status == status)
        rows = query.order_by(ApprovalRequest.created_at.desc()).limit(limit).all()
        return [_approval_request_to_dict(r) for r in rows]


def decide_approval_request(
    request_id: int, decision: str, comments: str | None, decided_by: str | None
) -> dict | None:
    """Records an accept/reject decision. decision must be 'approved' or 'rejected'."""
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")

    with Session(engine) as session:
        req = session.get(ApprovalRequest, request_id)
        if not req:
            return None
        req.status = decision  # type: ignore[assignment]
        req.comments = comments  # type: ignore[assignment]
        req.decided_by = decided_by  # type: ignore[assignment]
        req.decided_at = _utcnow()  # type: ignore[assignment]
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
