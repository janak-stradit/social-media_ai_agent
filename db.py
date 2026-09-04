"""
Database layer – synchronous SQLAlchemy + psycopg2.
Schema: social_media_agent
Tables: users, run_history
"""

# pylint: disable=not-callable,assignment-from-no-return
# SQLAlchemy's `func` proxy (func.count/func.coalesce/func.sum/...) is dynamic;
# pylint's static analysis can't see through it and misreports these two checks
# throughout this file. Known false positive, not scoped per-line for readability.

import json
import os
from datetime import datetime, timezone

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
    post_url: Mapped[str] = mapped_column(String(500), nullable=False)
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
            inserted += 1

        session.commit()
        return {"inserted": inserted, "skipped": skipped, "new_post_urls": new_post_urls}


def get_competitor_posts(platform: str | None = None, competitor: str | None = None, limit: int = 300) -> list[dict]:
    """Return previously-scraped competitor posts stored in the DB, newest first."""
    with Session(engine) as session:
        query = session.query(CompetitorPost)
        if platform:
            query = query.filter(CompetitorPost.platform == platform.lower())
        if competitor and competitor.lower() != "all":
            query = query.filter(CompetitorPost.competitor == competitor)

        order_col = func.coalesce(CompetitorPost.published_at, CompetitorPost.scraped_at, CompetitorPost.created_at)
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
