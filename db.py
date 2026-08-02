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

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:root@localhost:5432/postgres",
)
SCHEMA = "social_media_agent"

engine = create_engine(DATABASE_URL, pool_pre_ping=True, echo=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": SCHEMA}

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class RunHistory(Base):
    __tablename__ = "run_history"
    __table_args__ = {"schema": SCHEMA}

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey(f"{SCHEMA}.users.id"), nullable=True, index=True)
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
    with engine.connect() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
        conn.commit()
    Base.metadata.create_all(engine)

    with engine.connect() as conn:
        conn.execute(text(
            f'ALTER TABLE "{SCHEMA}".run_history '
            f'ADD COLUMN IF NOT EXISTS user_id INTEGER'
        ))
        conn.execute(text(
            f'ALTER TABLE "{SCHEMA}".run_history '
            f'ADD COLUMN IF NOT EXISTS tokens_used INTEGER DEFAULT 0'
        ))
        conn.execute(text(
            f'ALTER TABLE "{SCHEMA}".run_history '
            f'ADD COLUMN IF NOT EXISTS cost_usd DOUBLE PRECISION DEFAULT 0.0'
        ))
        conn.execute(text(
            f'ALTER TABLE "{SCHEMA}".run_history '
            f'ADD COLUMN IF NOT EXISTS is_archived BOOLEAN DEFAULT FALSE'
        ))
        conn.commit()


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


def create_user(name: str, email: str, password_hash: str) -> dict:
    with Session(engine) as session:
        row = User(name=name.strip(), email=email.strip().lower(), password_hash=password_hash)
        session.add(row)
        session.commit()
        session.refresh(row)
        return {"id": row.id, "name": row.name, "email": row.email}


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
    """Return aggregated token and cost metrics for a user."""
    with Session(engine) as session:
        result = session.query(
            func.count(RunHistory.id).label("total_runs"),
            func.coalesce(func.sum(RunHistory.tokens_used), 0).label("total_tokens"),
            func.coalesce(func.sum(RunHistory.cost_usd), 0.0).label("total_cost")
        ).filter(RunHistory.user_id == user_id).first()

        total_runs = result.total_runs if result else 0
        total_tokens = int(result.total_tokens) if result else 0
        total_cost = float(result.total_cost) if result else 0.0

        return {
            "total_runs": total_runs,
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6)
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
