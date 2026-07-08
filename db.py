"""
Database layer – synchronous SQLAlchemy + psycopg2.
Schema: social_media_agent
Table : social_media_agent.run_history
"""

import json
from datetime import datetime

from sqlalchemy import (
    create_engine, text,
    Column, Integer, String, Text, DateTime
)
from sqlalchemy.orm import DeclarativeBase, Session

# ── Connection ─────────────────────────────────────────────────────────────
# asyncpg is not usable in sync Flask; swap to psycopg2 driver
DATABASE_URL = "postgresql+psycopg2://postgres:postgres@localhost:5433/postgres"
SCHEMA = "social_media_agent"

engine = create_engine(DATABASE_URL, pool_pre_ping=True, echo=False)


# ── ORM Base ───────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


class RunHistory(Base):
    __tablename__ = "run_history"
    __table_args__ = {"schema": SCHEMA}

    id          = Column(Integer, primary_key=True, autoincrement=True)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    story       = Column(Text, nullable=False)
    tone        = Column(String(64), nullable=True)
    platforms   = Column(Text, nullable=False)   # JSON array stored as text
    content     = Column(Text, nullable=False)   # Full response JSON


# ── Schema + Table bootstrap ───────────────────────────────────────────────
def init_db():
    """Create schema and tables if they don't exist."""
    with engine.connect() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
        conn.commit()
    Base.metadata.create_all(engine)


# ── CRUD helpers ───────────────────────────────────────────────────────────
def save_run(story: str, tone: str, platforms: list, content: dict) -> int:
    """Persist a generation run and return its id."""
    with Session(engine) as session:
        row = RunHistory(
            story=story,
            tone=tone or "Auto",
            platforms=json.dumps(platforms),
            content=json.dumps(content),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def get_history(limit: int = 20) -> list[dict]:
    """Return the most recent runs, newest first."""
    with Session(engine) as session:
        rows = (
            session.query(RunHistory)
            .order_by(RunHistory.created_at.desc())
            .limit(limit)
            .all()
        )
        result = []
        for r in rows:
            result.append({
                "id": r.id,
                "timestamp": r.created_at.strftime("%Y-%m-%d %H:%M"),
                "story": r.story[:120] + ("..." if len(r.story) > 120 else ""),
                "tone": r.tone,
                "platforms": json.loads(r.platforms),
                "content": json.loads(r.content),
            })
        return result


def get_run_by_id(run_id: int) -> dict | None:
    """Return a single run by primary key."""
    with Session(engine) as session:
        row = session.get(RunHistory, run_id)
        if not row:
            return None
        return {
            "id": row.id,
            "timestamp": row.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "story": row.story,
            "tone": row.tone,
            "platforms": json.loads(row.platforms),
            "content": json.loads(row.content),
        }
