"""Pricing plans (db.DEFAULT_PLANS, PRICING.md): monthly allowances, daily
caps, packs, and what each plan lets a user generate. Nothing external is
called - the generators are faked."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

import db


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


def _user(plan=None, is_admin=False, account_type=None):
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash", is_admin=is_admin)
    with Session(db.engine) as session:
        session.query(db.User).filter(db.User.id == user["id"]).update(
            {"email_verified": True, "onboarding_completed": True, "account_type": account_type}
        )
        session.commit()
    if plan:
        db.assign_plan(user["id"], plan)
    return user


def _login(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


def _images(user, n, kind="image"):
    for _ in range(n):
        db.log_image_generation(user["id"], 0.55, kind=kind)


def _posts(user, n):
    for _ in range(n):
        db.save_run(story="A post", tone="", platforms=["linkedin"], content={"linkedin": {}}, user_id=user["id"])


def test_plans_are_seeded_from_the_pricing_table():
    plans = {p["slug"]: p for p in db.list_plans()}
    assert list(plans) == ["free", "starter", "growth", "pro", "enterprise"]
    assert (plans["starter"]["price_inr"], plans["starter"]["price_usd"]) == (1599, 19)
    assert (plans["growth"]["posts_month"], plans["growth"]["images_month"], plans["growth"]["images_day"]) == (
        200,
        25,
        10,
    )
    assert plans["enterprise"]["posts_month"] is None and plans["pro"]["video_pack_discount"] == 20


def test_new_users_are_on_free_and_enterprise_accounts_on_enterprise():
    assert db.get_plan_usage(_user()["id"])["plan"]["slug"] == "free"
    assert db.get_plan_usage(_user(account_type="enterprise")["id"])["plan"]["slug"] == "enterprise"


def test_monthly_allowance_counts_posts_images_and_edits():
    user = _user("starter")
    _posts(user, 2)
    _images(user, 2)
    _images(user, 1, kind="edit")

    usage = db.get_plan_usage(user["id"])

    assert usage["posts"] == {"used": 2, "limit": 60, "remaining": 58}
    assert usage["images"]["used"] == 3 and usage["images"]["remaining"] == 7


def test_daily_cap_and_monthly_allowance_both_limit_images():
    user = _user("free")  # 1 image a day, 3 a month
    assert db.get_image_quota(user["id"])["remaining"] == 1
    _images(user, 1)
    quota = db.get_image_quota(user["id"])
    assert quota["remaining"] == 0 and quota["blocked_by"] == "day"


def test_packs_are_used_only_after_the_allowance(monkeypatch):
    user = _user("free")
    with Session(db.engine) as session:  # no daily cap for this test
        session.query(db.User).filter(db.User.id == user["id"]).update({"image_limit": db.IMAGE_UNLIMITED})
        session.commit()
    db.grant_pack(user["id"], "image", 2, "Diwali top-up")
    _images(user, 3)  # the plan's 3

    usage = db.get_plan_usage(user["id"])
    assert usage["images"]["pack_remaining"] == 2 and usage["images"]["remaining"] == 2
    _images(user, 2)  # from the pack
    usage = db.get_plan_usage(user["id"])
    assert usage["images"]["pack_remaining"] == 0 and usage["images"]["remaining"] == 0
    assert db.get_image_quota(user["id"])["blocked_by"] == "month"


def test_videos_need_a_pack_on_paid_plans():
    user = _user("growth")
    db.set_user_video_access(user["id"], True)
    quota = db.get_video_quota(user["id"])
    assert quota["enabled"] and quota["remaining"] == 0 and quota["blocked_by"] == "month"

    db.grant_pack(user["id"], "video", 2)
    quota = db.get_video_quota(user["id"])
    assert quota["remaining"] == 2 and quota["limit"] == 2  # 2 a day on Growth
    _images(user, 1, kind="video")
    assert db.get_plan_usage(user["id"])["videos"]["pack_remaining"] == 1


def test_enterprise_and_admins_are_unlimited():
    for user in (_user("enterprise"), _user(is_admin=True)):
        _images(user, 5)
        assert db.get_image_quota(user["id"])["unlimited"] is True


def test_period_resets_on_the_plan_day():
    user = _user()
    with Session(db.engine) as session:
        row = session.get(db.User, user["id"])
        row.plan_started_at = datetime(2026, 9, 15, 10, 0)
        session.commit()
        row = session.get(db.User, user["id"])
        start, end = db.plan_period(row, datetime(2026, 10, 3, tzinfo=timezone.utc))
        assert (start.date().isoformat(), end.date().isoformat()) == ("2026-09-15", "2026-10-15")
        row.plan_started_at = datetime(2026, 1, 31)
        start, end = db.plan_period(row, datetime(2026, 2, 20, tzinfo=timezone.utc))
        assert (start.date().isoformat(), end.date().isoformat()) == ("2026-01-31", "2026-02-28")  # short month


def test_credit_cap_applies_to_the_free_plan_only(app):
    free, paid = _user("free"), _user("starter")
    assert _login(app, free).get("/api/metrics/usage").get_json()["credit_capped"] is True
    assert _login(app, paid).get("/api/metrics/usage").get_json()["credit_capped"] is False


def test_posts_are_blocked_when_the_month_is_used(app):
    user = _user("free")
    _posts(user, 10)
    res = _login(app, user).post("/api/generate", json={"story": "A new post", "platforms": ["linkedin"]})
    assert res.status_code == 403 and res.get_json()["code"] == "plan_posts_exhausted"
    assert "Free plan" in res.get_json()["error"]


def test_admin_assigns_plans_grants_packs_and_edits_prices(app):
    admin = _login(app, _user(is_admin=True))
    user = _user()

    res = admin.put(f"/api/admin/users/{user['id']}/plan", json={"plan": "pro"}).get_json()
    assert res["usage"]["plan"]["slug"] == "pro"
    res = admin.post(
        f"/api/admin/users/{user['id']}/packs", json={"kind": "video", "quantity": 5, "note": "Launch"}
    ).get_json()
    assert res["usage"]["videos"]["pack_remaining"] == 5
    assert admin.post(f"/api/admin/users/{user['id']}/packs", json={"kind": "gold", "quantity": 1}).status_code == 400
    assert admin.put(f"/api/admin/users/{user['id']}/plan", json={"plan": "platinum"}).status_code == 404

    edited = admin.put("/api/admin/plans/starter", json={"price_inr": 1499, "images_month": 12}).get_json()["plan"]
    assert (edited["price_inr"], edited["images_month"]) == (1499, 12)
    db.update_plan("starter", {"price_inr": 1599, "images_month": 10})  # put it back for other tests
    assert _login(app, user).put("/api/admin/plans/starter", json={"price_inr": 1}).status_code in (401, 403)


def test_user_sees_their_plan_and_all_plans(app):
    client = _login(app, _user("growth"))
    assert client.get("/api/me/plan").get_json()["usage"]["plan"]["name"] == "Growth"
    assert len(client.get("/api/plans").get_json()["plans"]) == 5


def test_plan_editor_sends_text_values_and_empty_for_unlimited(app):
    admin = _login(app, _user(is_admin=True))
    res = admin.put(
        "/api/admin/plans/growth",
        json={"price_inr": "4499", "posts_month": None, "approvals": False, "best_for": "Shops and clinics"},
    ).get_json()
    plan = res["plan"]
    assert (plan["price_inr"], plan["posts_month"], plan["approvals"], plan["best_for"]) == (
        4499,
        None,
        False,
        "Shops and clinics",
    )
    assert admin.put("/api/admin/plans/growth", json={"images_day": "-1"}).status_code == 400
    assert admin.put("/api/admin/plans/growth", json={"name": ""}).status_code == 400
    db.update_plan("growth", {"price_inr": 3999, "posts_month": 200, "approvals": True, "best_for": "Small businesses"})


def test_users_table_carries_each_users_plan():
    user = _user("pro")
    row = next(u for u in db.get_all_users_credit_summary() if u["id"] == user["id"])
    assert row["plan"] == "pro"


# ── What each plan lets the user see and use ─────────────────────────────


def test_features_follow_the_plan():
    assert db.get_user_features(_user("free")["id"])["approvals"] is False
    assert db.get_user_features(_user("free")["id"])["video"] is False
    starter = db.get_user_features(_user("starter")["id"])
    assert (starter["approvals"], starter["video"], starter["multiple_brands"]) == (False, True, False)
    growth = db.get_user_features(_user("growth")["id"])
    assert growth["approvals"] is True and growth["video"] is True
    assert db.get_user_features(_user("pro")["id"])["multiple_brands"] is True
    assert all(v for k, v in db.get_user_features(_user(is_admin=True)["id"]).items() if k in db.PLAN_FEATURES)
    assert (
        db.cheapest_plan_with("approvals")["slug"] == "growth" and db.cheapest_plan_with("video")["slug"] == "starter"
    )


def test_approvals_are_refused_below_growth(app):
    res = _login(app, _user("starter")).post("/api/approval-requests", json={"pipeline_client_id": "x", "items": []})
    body = res.get_json()
    assert res.status_code == 403 and body["code"] == "plan_feature_locked" and body["upgrade_plan"] == "growth"
    assert "Growth plan" in body["error"] and "Starter" in body["error"]
    assert _login(app, _user("starter")).post("/api/send-approval-email", json={}).status_code == 403
    # Growth gets past the plan check (and on to the request's own validation)
    assert _login(app, _user("growth")).post("/api/approval-requests", json={}).status_code != 403


def test_studio_chat_gets_the_features_and_the_plan_to_upgrade_to(app):
    r = _login(app, _user("free")).get("/api/me/image-quota").get_json()
    assert r["features"]["approvals"] is False and r["upgrade_plans"] == {"approvals": "Growth", "video": "Starter"}
    r = _login(app, _user("pro")).get("/api/me/image-quota").get_json()
    assert r["upgrade_plans"] == {} and r["features"]["plan"] == "pro"
    usage = _login(app, _user("growth")).get("/api/metrics/usage").get_json()
    assert usage["plan_features"]["approvals"] is True
