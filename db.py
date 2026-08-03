"""
Database layer – synchronous SQLAlchemy + psycopg2.
Schema: social_media_agent
Tables: users, run_history
"""

import json
import os
from datetime import datetime

from sqlalchemy import (
    create_engine, text,
    Column, Integer, String, Text, DateTime, ForeignKey, Float, Boolean
)
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.sql import func

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    try:
        import psycopg2
        DATABASE_URL = "postgresql+psycopg2://postgres:root@localhost:5432/postgres"
    except ImportError:
        DATABASE_URL = "sqlite:///social_media_agent.db"

IS_SQLITE = DATABASE_URL.startswith("sqlite")
SCHEMA = "social_media_agent"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True if not IS_SQLITE else False,
    connect_args={"check_same_thread": False} if IS_SQLITE else {},
    echo=False
)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    credit_limit = Column(Float, default=10.0, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)


class CreditRequest(Base):
    __tablename__ = "credit_requests"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey(f"{SCHEMA}.users.id" if not IS_SQLITE else "users.id"), nullable=False, index=True)
    requested_amount = Column(Float, nullable=False)
    reason = Column(Text, nullable=True)
    status = Column(String(32), default="pending", nullable=False)  # pending, approved, rejected
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class RunHistory(Base):
    __tablename__ = "run_history"
    __table_args__ = {"schema": SCHEMA} if not IS_SQLITE else {}

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey(f"{SCHEMA}.users.id" if not IS_SQLITE else "users.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    story = Column(Text, nullable=False)
    tone = Column(String(64), nullable=True)
    platforms = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    tokens_used = Column(Integer, default=0, nullable=True)
    cost_usd = Column(Float, default=0.0, nullable=True)
    is_archived = Column(Boolean, default=False, nullable=True)


def init_db():
    """Create schema, tables, and apply lightweight migrations."""
    if not IS_SQLITE:
        with engine.connect() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
            conn.commit()
            
    Base.metadata.create_all(engine)

    with engine.connect() as conn:
        run_tbl = f'"{SCHEMA}".run_history' if not IS_SQLITE else 'run_history'
        usr_tbl = f'"{SCHEMA}".users' if not IS_SQLITE else 'users'
        
        try:
            conn.execute(text(f'ALTER TABLE {run_tbl} ADD COLUMN user_id INTEGER'))
        except Exception:
            pass
        try:
            conn.execute(text(f'ALTER TABLE {run_tbl} ADD COLUMN tokens_used INTEGER DEFAULT 0'))
        except Exception:
            pass
        try:
            conn.execute(text(f'ALTER TABLE {run_tbl} ADD COLUMN cost_usd DOUBLE PRECISION DEFAULT 0.0'))
        except Exception:
            pass
        try:
            conn.execute(text(f'ALTER TABLE {run_tbl} ADD COLUMN is_archived BOOLEAN DEFAULT FALSE'))
        except Exception:
            pass
        try:
            conn.execute(text(f'ALTER TABLE {usr_tbl} ADD COLUMN credit_limit DOUBLE PRECISION DEFAULT 10.0'))
        except Exception:
            pass
        try:
            conn.execute(text(f'ALTER TABLE {usr_tbl} ADD COLUMN is_admin BOOLEAN DEFAULT FALSE'))
        except Exception:
            pass
        conn.commit()

    # Seed default admin if no admin user exists
    try:
        from werkzeug.security import generate_password_hash
        with Session(engine) as session:
            admin_user = session.query(User).filter(User.is_admin == True).first()
            if not admin_user:
                # Check if admin email exists
                existing = session.query(User).filter(User.email == "admin@contentai.com").first()
                if existing:
                    existing.is_admin = True
                    existing.credit_limit = max(existing.credit_limit or 10.0, 1000.0)
                else:
                    new_admin = User(
                        name="System Admin",
                        email="admin@contentai.com",
                        password_hash=generate_password_hash("admin123"),
                        credit_limit=1000.0,
                        is_admin=True
                    )
                    session.add(new_admin)
                session.commit()
                print("[DB] Default Admin user (admin@contentai.com / admin123) initialized.")
    except Exception as seed_err:
        print(f"[DB] Warning seeding admin user: {seed_err}")


def _serialize_run(row: RunHistory, truncate_story: bool = False) -> dict:
    story = row.story
    if truncate_story and len(story) > 120:
        story = story[:120] + "..."
    return {
        "id": row.id,
        "timestamp": row.created_at.strftime("%Y-%m-%d %H:%M" if truncate_story else "%Y-%m-%d %H:%M:%S"),
        "story": story,
        "tone": row.tone,
        "platforms": json.loads(row.platforms),
        "content": json.loads(row.content),
        "tokens_used": row.tokens_used or 0,
        "cost_usd": round(row.cost_usd or 0.0, 6),
        "is_archived": bool(getattr(row, "is_archived", False))
    }


def create_user(name: str, email: str, password_hash: str, is_admin: bool = False, credit_limit: float = 10.0) -> dict:
    with Session(engine) as session:
        row = User(name=name.strip(), email=email.strip().lower(), password_hash=password_hash, is_admin=is_admin, credit_limit=credit_limit)
        session.add(row)
        session.commit()
        session.refresh(row)
        return {"id": row.id, "name": row.name, "email": row.email, "is_admin": row.is_admin, "credit_limit": row.credit_limit}


def get_user_by_email(email: str) -> User | None:
    with Session(engine) as session:
        return session.query(User).filter(User.email == email.strip().lower()).first()


def get_user_by_id(user_id: int) -> User | None:
    with Session(engine) as session:
        return session.get(User, user_id)


def save_run(story: str, tone: str, platforms: list, content: dict, user_id: int, tokens_used: int = 0, cost_usd: float = 0.0) -> int:
    with Session(engine) as session:
        row = RunHistory(
            user_id=user_id,
            story=story,
            tone=tone or "Auto",
            platforms=json.dumps(platforms),
            content=json.dumps(content),
            tokens_used=tokens_used,
            cost_usd=cost_usd,
            is_archived=False
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def update_run_content(run_id: int, content: dict, user_id: int | None = None) -> bool:
    with Session(engine) as session:
        row = session.get(RunHistory, run_id)
        if not row:
            return False
        if user_id is not None and row.user_id != user_id:
            return False
        row.content = json.dumps(content)
        session.commit()
        return True


def archive_run(run_id: int, user_id: int | None = None) -> bool:
    with Session(engine) as session:
        row = session.get(RunHistory, run_id)
        if not row:
            return False
        if user_id is not None and row.user_id != user_id:
            return False
        row.is_archived = True
        session.commit()
        return True


def unarchive_run(run_id: int, user_id: int | None = None) -> bool:
    with Session(engine) as session:
        row = session.get(RunHistory, run_id)
        if not row:
            return False
        if user_id is not None and row.user_id != user_id:
            return False
        row.is_archived = False
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
            query = query.filter((RunHistory.is_archived == False) | (RunHistory.is_archived.is_(None)))
        else:
            query = query.filter(RunHistory.is_archived == True)
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
        credit_limit = float(user.credit_limit) if user and user.credit_limit is not None else 10.0
        is_admin = bool(user.is_admin) if user else False

        result = session.query(
            func.count(RunHistory.id).label("total_runs"),
            func.coalesce(func.sum(RunHistory.tokens_used), 0).label("total_tokens"),
            func.coalesce(func.sum(RunHistory.cost_usd), 0.0).label("total_cost")
        ).filter(RunHistory.user_id == user_id).first()

        total_runs = result.total_runs if result else 0
        total_tokens = int(result.total_tokens) if result else 0
        used_cost = float(result.total_cost) if result else 0.0

        remaining = max(0.0, credit_limit - used_cost)

        # Check pending request
        pending_req = session.query(CreditRequest).filter(
            CreditRequest.user_id == user_id,
            CreditRequest.status == "pending"
        ).first()

        return {
            "total_runs": total_runs,
            "total_tokens": total_tokens,
            "total_cost_usd": round(used_cost, 6),
            "used_credits": round(used_cost, 4),
            "credit_limit": round(credit_limit, 2),
            "remaining_credits": round(remaining, 4),
            "is_admin": is_admin,
            "has_pending_request": pending_req is not None,
            "pending_request": {
                "id": pending_req.id,
                "requested_amount": pending_req.requested_amount,
                "reason": pending_req.reason,
                "created_at": pending_req.created_at.strftime("%Y-%m-%d %H:%M")
            } if pending_req else None
        }


def assign_orphan_runs_to_user(email: str) -> dict:
    """Assign all runs without a user to the account matching email."""
    user = get_user_by_email(email)
    if not user:
        raise ValueError(f"No user found for email: {email}")

    with engine.begin() as conn:
        result = conn.execute(
            text(f'UPDATE "{SCHEMA}".run_history SET user_id = :uid WHERE user_id IS NULL'),
            {"uid": user.id},
        )
        updated = result.rowcount or 0

    return {
        "email": user.email,
        "user_id": user.id,
        "assigned_runs": updated,
        "total_runs": len(get_history(limit=1000, user_id=user.id)),
    }


# ── Credit & Admin Database Helper Functions ───────────────────────────────

def create_credit_request(user_id: int, requested_amount: float, reason: str = "") -> dict:
    """Create a new credit extension request for a user."""
    with Session(engine) as session:
        # Check if there is already a pending request
        existing = session.query(CreditRequest).filter(
            CreditRequest.user_id == user_id,
            CreditRequest.status == "pending"
        ).first()
        if existing:
            existing.requested_amount = requested_amount
            existing.reason = reason
            session.commit()
            return {
                "id": existing.id,
                "requested_amount": existing.requested_amount,
                "reason": existing.reason,
                "status": existing.status,
                "updated": True
            }

        req = CreditRequest(
            user_id=user_id,
            requested_amount=requested_amount,
            reason=reason,
            status="pending"
        )
        session.add(req)
        session.commit()
        session.refresh(req)
        return {
            "id": req.id,
            "requested_amount": req.requested_amount,
            "reason": req.reason,
            "status": req.status,
            "created_at": req.created_at.strftime("%Y-%m-%d %H:%M")
        }


def get_user_credit_requests(user_id: int) -> list[dict]:
    with Session(engine) as session:
        rows = session.query(CreditRequest).filter(
            CreditRequest.user_id == user_id
        ).order_by(CreditRequest.created_at.desc()).all()
        return [{
            "id": r.id,
            "requested_amount": r.requested_amount,
            "reason": r.reason,
            "status": r.status,
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M")
        } for r in rows]


def get_all_credit_requests(status_filter: str | None = None) -> list[dict]:
    """Return all credit extension requests with user details (for admin)."""
    with Session(engine) as session:
        query = session.query(CreditRequest, User).join(User, CreditRequest.user_id == User.id)
        if status_filter:
            query = query.filter(CreditRequest.status == status_filter)
        query = query.order_by(CreditRequest.created_at.desc())

        results = []
        for req, user in query.all():
            results.append({
                "id": req.id,
                "user_id": user.id,
                "user_name": user.name,
                "user_email": user.email,
                "current_limit": user.credit_limit,
                "requested_amount": req.requested_amount,
                "reason": req.reason,
                "status": req.status,
                "created_at": req.created_at.strftime("%Y-%m-%d %H:%M")
            })
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
            "status": "approved"
        }


def reject_credit_request(request_id: int) -> dict | None:
    """Reject a credit extension request."""
    with Session(engine) as session:
        req = session.get(CreditRequest, request_id)
        if not req or req.status != "pending":
            return None

        req.status = "rejected"
        session.commit()
        return {
            "request_id": req.id,
            "status": "rejected"
        }


def update_user_credit_limit(user_id: int, new_limit: float = None, add_amount: float = None) -> dict | None:
    """Update or add to a user's credit limit (admin action)."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            return None

        if new_limit is not None:
            user.credit_limit = max(0.0, float(new_limit))
        elif add_amount is not None:
            user.credit_limit = max(0.0, (user.credit_limit or 10.0) + float(add_amount))

        session.commit()
        return {
            "user_id": user.id,
            "user_name": user.name,
            "user_email": user.email,
            "credit_limit": round(user.credit_limit, 2)
        }


def get_all_users_credit_summary() -> list[dict]:
    """Return credit summaries for all registered users (for admin management)."""
    with Session(engine) as session:
        users = session.query(User).order_by(User.created_at.asc()).all()
        summaries = []
        for u in users:
            # Query usage cost for this user
            cost_res = session.query(
                func.coalesce(func.sum(RunHistory.cost_usd), 0.0).label("used_cost"),
                func.count(RunHistory.id).label("total_runs")
            ).filter(RunHistory.user_id == u.id).first()

            used_cost = float(cost_res.used_cost) if cost_res else 0.0
            total_runs = cost_res.total_runs if cost_res else 0
            limit = float(u.credit_limit) if u.credit_limit is not None else 10.0
            remaining = max(0.0, limit - used_cost)

            # Check pending request
            has_pending = session.query(CreditRequest).filter(
                CreditRequest.user_id == u.id,
                CreditRequest.status == "pending"
            ).first() is not None

            summaries.append({
                "id": u.id,
                "name": u.name,
                "email": u.email,
                "is_admin": bool(u.is_admin),
                "credit_limit": round(limit, 2),
                "used_credits": round(used_cost, 4),
                "remaining_credits": round(remaining, 4),
                "total_runs": total_runs,
                "has_pending_request": has_pending,
                "created_at": u.created_at.strftime("%Y-%m-%d")
            })
        return summaries


def get_global_cost_history(limit: int = 100) -> list[dict]:
    """Return all cost history runs across all users with user info (for admin)."""
    with Session(engine) as session:
        rows = session.query(RunHistory, User)\
            .outerjoin(User, RunHistory.user_id == User.id)\
            .order_by(RunHistory.created_at.desc())\
            .limit(limit).all()

        history = []
        for run, user in rows:
            story_snippet = run.story[:100] + "..." if len(run.story) > 100 else run.story
            history.append({
                "id": run.id,
                "user_id": run.user_id,
                "user_name": user.name if user else "Unknown",
                "user_email": user.email if user else "N/A",
                "timestamp": run.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "story": story_snippet,
                "tone": run.tone,
                "platforms": json.loads(run.platforms) if run.platforms else [],
                "tokens_used": run.tokens_used or 0,
                "cost_usd": round(run.cost_usd or 0.0, 6)
            })
        return history

