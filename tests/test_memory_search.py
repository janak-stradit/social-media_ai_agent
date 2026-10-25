"""Memory search reads only the signed-in user's own memories, filtered in the
database (services/memory_service.py), so it stays fast as users grow."""

import json
import uuid

from sqlalchemy.orm import Session

import db
from services import memory_service as ms


class FakeEmbeddings:
    def get_embedding(self, text):
        return [1.0, 0.0] if "truck" in text else [0.0, 1.0]


def _service(monkeypatch):
    svc = ms.MemoryService.__new__(ms.MemoryService)
    svc.__dict__.update(enabled=True, embedding_service=FakeEmbeddings())
    return svc


def _uid():
    return db.create_user("P", f"{uuid.uuid4().hex[:8]}@example.com", "h")["id"]


def test_each_user_finds_only_their_own_memories(monkeypatch):
    svc = _service(monkeypatch)
    mine, theirs = _uid(), _uid()
    svc.store_campaign_run(run_id=f"m{mine}", story="truck tracking", content={}, user_id=mine, tone="", platforms=["linkedin"])
    svc.store_campaign_run(run_id=f"t{theirs}", story="truck fleet secrets", content={}, user_id=theirs, tone="", platforms=["linkedin"])
    found = svc.retrieve_context("truck", user_id=mine, n_results=5)
    assert [m["metadata"]["user_id"] for m in found] == [str(mine)]
    with Session(db.engine) as s:
        assert s.get(db.MemoryEmbedding, f"run_m{mine}").user_id == mine


def test_only_the_most_recent_memories_are_compared(monkeypatch):
    svc = _service(monkeypatch)
    user = _uid()
    monkeypatch.setattr(ms, "MAX_MEMORIES_SEARCHED", 3)
    for i in range(5):
        svc.store_campaign_run(run_id=f"cap{user}-{i}", story=f"truck post {i}", content={}, user_id=user, tone="", platforms=[])
    assert len(svc.retrieve_context("truck", user_id=user, n_results=10)) == 3


def test_older_memories_get_their_owner_from_metadata():
    user = _uid()
    with Session(db.engine) as s:
        s.add(db.MemoryEmbedding(id=f"old-{user}", content="x", embedding_array="[1, 0]",
                                 metadata_json=json.dumps({"user_id": str(user)}), user_id=None))
        s.add(db.MemoryEmbedding(id=f"orphan-{user}", content="x", embedding_array="[1, 0]", metadata_json="{}", user_id=None))
        s.commit()
    assert db._backfill_memory_owners() >= 2
    with Session(db.engine) as s:
        assert s.get(db.MemoryEmbedding, f"old-{user}").user_id == user
        assert s.get(db.MemoryEmbedding, f"orphan-{user}").user_id == 0
