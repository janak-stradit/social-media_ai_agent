"""Admin -> Global Cost History: filters and pagination (runs + image charges
made outside a run, merged and paged in the database)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

import db


@pytest.fixture
def seeded():
    """Two users; 30 runs for Priya (every 3rd on Instagram with an image), 5 for
    Sam, and 3 image charges outside any run for Sam. Ages: run i is i hours old."""
    with db.Session(db.engine) as s:
        s.execute(delete(db.ImageGeneration))
        s.execute(delete(db.RunHistory))
        s.commit()
    priya = db.create_user("Priya Shah", f"priya-{uuid.uuid4().hex[:6]}@loadmate.ae", "h")
    sam = db.create_user("Sam Carter", f"sam-{uuid.uuid4().hex[:6]}@freightco.com", "h")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with db.Session(db.engine) as s:
        for i in range(30):
            instagram = i % 3 == 0
            run = db.RunHistory(
                user_id=priya["id"], story=f"Priya post {i} about GPS tracking", tone="Auto",
                platforms='["instagram"]' if instagram else '["linkedin"]', content="{}",
                tokens_used=100, cost_usd=0.01 * (i + 1), created_at=now - timedelta(hours=i),
            )
            s.add(run)
            s.flush()
            if instagram:
                s.add(db.ImageGeneration(user_id=priya["id"], run_id=run.id, kind="image", cost_usd=0.55,
                                         media_url=f"/static/uploads/p{i}.jpg", created_at=run.created_at))
        for i in range(5):
            s.add(db.RunHistory(user_id=sam["id"], story=f"Sam Diwali greeting {i}", tone="Auto", platforms='["facebook"]',
                                content="{}", tokens_used=50, cost_usd=0.02, created_at=now - timedelta(days=10 + i)))
        for i in range(3):
            s.add(db.ImageGeneration(user_id=sam["id"], run_id=None, kind="image", platform="instagram", cost_usd=0.55,
                                     description=f"Dashboard image {i}", media_url=f"/static/uploads/s{i}.jpg",
                                     created_at=now - timedelta(minutes=30 + i)))
        s.commit()
    return priya, sam


def test_pages_cover_every_row_once(seeded):
    seen = []
    first = db.get_global_cost_history(page=1, page_size=10)
    assert first["total"] == 38 and first["pages"] == 4
    for page in range(1, 5):
        seen += [(h.get("id"), h.get("charge_id")) for h in db.get_global_cost_history(page=page, page_size=10)["history"]]
    assert len(seen) == 38 and len(set(seen)) == 38


def test_newest_first_mixes_runs_and_charges(seeded):
    rows = db.get_global_cost_history(page=1, page_size=5)["history"]
    times = [h["timestamp"] for h in rows]
    assert times == sorted(times, reverse=True)
    assert rows[0]["story"] == "Priya post 0 about GPS tracking"  # newest
    assert rows[1].get("kind") == "media_charge"  # 30 min old, before run 1 (1 h)


def test_search_user_prompt_and_run_id(seeded):
    _ = seeded
    assert db.get_global_cost_history(q="Sam Carter")["total"] == 8  # 5 runs + 3 charges
    assert db.get_global_cost_history(q="loadmate.ae")["total"] == 30
    assert db.get_global_cost_history(q="diwali")["total"] == 5
    some_run = db.get_global_cost_history(q="Priya post 7 ")["history"][0]["id"]
    found = db.get_global_cost_history(q=f"#{some_run}")["history"]
    assert any(h["id"] == some_run for h in found)


def test_user_type_platform_cost_and_date_filters(seeded):
    _, sam = seeded
    assert db.get_global_cost_history(user_id=sam["id"])["total"] == 8
    assert db.get_global_cost_history(kind="charges")["total"] == 3
    assert db.get_global_cost_history(kind="runs")["total"] == 35
    assert db.get_global_cost_history(kind="runs_with_images")["total"] == 10
    assert db.get_global_cost_history(platform="instagram")["total"] == 13  # 10 runs + 3 charges
    assert db.get_global_cost_history(min_cost=0.25)["total"] == 6 + 3  # runs 24..29 + charges
    last_week = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    assert db.get_global_cost_history(date_from=last_week)["total"] == 33  # Sam's runs are older
    assert db.get_global_cost_history(date_to=last_week)["total"] == 5


def test_filtered_totals_and_cost_sort(seeded):
    result = db.get_global_cost_history(kind="charges")
    assert result["filtered"] == {"count": 3, "cost_usd": 1.65, "tokens": 0}
    top = db.get_global_cost_history(sort="cost", page_size=4)["history"]
    assert [h["cost_usd"] for h in top] == sorted([h["cost_usd"] for h in top], reverse=True)
    assert top[0]["cost_usd"] == 0.55


def test_api_parses_filters_and_requires_admin(seeded):
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    admin = db.create_user("Admin", f"admin-{uuid.uuid4().hex[:6]}@example.com", "h", is_admin=True)
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = admin["id"]
    today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
    r = client.get(f"/api/admin/cost-history?page=2&page_size=5&type=runs&platform=linkedin&date_to={today}").get_json()
    assert r["success"] and r["page"] == 2 and r["page_size"] == 5 and r["total"] == 20
    assert len(r["history"]) == 5 and "summary" in r
    # bad values fall back instead of failing
    assert client.get("/api/admin/cost-history?page=x&min_cost=abc&date_from=nope&sort=weird").get_json()["success"]

    user_client = app.test_client()
    with user_client.session_transaction() as s:
        s["user_id"] = seeded[0]["id"]
    assert user_client.get("/api/admin/cost-history").status_code in (401, 403)
