"""What works for this brand (services/insights_service.py): the traits read
from each posted item, which differences are clear enough to report, the
notes for the caption writer and the endpoint. Posts are scored at 48 hours
(db.PostMetricSnapshot), as in tests/test_post_metrics.py."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

import db
from services import insights_service
from services.insights_service import compare, traits


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


def _post(platform, score, caption="We cut delivery times in half.", media="none", images=None, hashtags=None):
    return {"platform": platform, "score": score,
            "item": {"platform": platform, "caption": caption, "media": media, "images": images or [],
                     "hashtags": hashtags or []}}


def test_traits_of_a_post():
    assert traits({"caption": "Is your clinic losing patients?\n\nHere is how.", "media": "carousel",
                   "images": ["a", "b"], "hashtags": ["#a", "#b", "#c"]}) == \
        {"format": "carousel", "opening": "question", "length": "short", "hashtags": "some"}
    assert traits({"caption": "3 ways to save tax " + "x" * 1200, "media": "single", "images": ["a"]})["opening"] == "number"
    long_post = traits({"caption": "We moved offices. " + "y" * 1200, "images": [], "hashtags": [f"#t{n}" for n in range(8)]})
    assert long_post == {"format": "text", "opening": "statement", "length": "long", "hashtags": "many"}
    assert traits({"caption": "Hello #one #two #three #four", "media": "none"})["hashtags"] == "some"  # tags in the caption


def test_a_clear_difference_is_reported_with_its_size():
    posts = [_post("instagram", s, media="carousel", images=["a", "b"]) for s in (180, 200, 220)]
    posts += [_post("instagram", s, media="single", images=["a"]) for s in (90, 100, 110)]
    found = {(f["trait"], f["value"]): f for f in compare(posts)}
    assert found[("format", "carousel")]["lift"] == 2.0
    assert found[("format", "carousel")]["text"] == "Carousels get 2.0× the engagement of your other Instagram posts"
    assert ("format", "image") not in found  # 0.5x: the same comparison from the other side, said once
    assert ("opening", "statement") not in found  # every post opens the same way: nothing to compare


def test_a_does_worse_finding_shows_when_nothing_in_its_trait_does_better():
    posts = [_post("facebook", s, caption="x" * 1200) for s in (40, 50, 60)]  # long
    posts += [_post("facebook", 100, caption="x" * 200)] * 2  # short: too few to judge
    posts += [_post("facebook", 100, caption="x" * 600)] * 2  # medium: too few to judge
    found = {(f["trait"], f["value"]): f["lift"] for f in compare(posts)}
    assert found == {("length", "long"): 0.5}


def test_small_or_unclear_differences_are_not_reported():
    few = [_post("linkedin", 300, caption="Why now?")] * 2 + [_post("linkedin", 100)] * 5
    assert compare(few) == []  # only 2 question openings
    close = [_post("linkedin", 110, caption="Why now?")] * 3 + [_post("linkedin", 100)] * 3
    assert compare(close) == []  # 1.1x is noise
    mixed = [_post("linkedin", 300, caption="Why now?")] * 3 + [_post("facebook", 100)] * 3
    assert compare(mixed) == []  # platforms are never compared with each other


def _published(user, platform, caption, engagement, days_ago=10):
    post_id = f"{platform[:2]}_{uuid.uuid4().hex[:8]}"
    scheduled = db.create_scheduled_post(user["id"], [platform], datetime.now(timezone.utc) - timedelta(days=days_ago),
                                         {"items": [{"platform": platform, "caption": caption, "media": "none"}]})
    db.register_published_post(user["id"], platform, post_id, None, caption, "scheduled", scheduled["id"])
    with Session(db.engine) as session:
        # published, as it says: a pending one in the past would be picked up by other tests' publish_due
        session.query(db.ScheduledPost).filter(db.ScheduledPost.id == scheduled["id"]).update({"status": "published"})
        row = session.query(db.PublishedPost).filter(db.PublishedPost.platform_post_id == post_id).one()
        row.published_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(tzinfo=None)
        session.add(db.PostMetricSnapshot(post_id=row.id, age_hours=6, metrics="{}", engagement=1))
        session.add(db.PostMetricSnapshot(post_id=row.id, age_hours=48, metrics="{}", engagement=engagement))
        session.commit()


def _brand_with_history():
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")
    for n, score in enumerate((150, 160, 170)):
        _published(user, "linkedin", f"Are you ready for the new tax year? Part {n}", score, days_ago=5 + n)
    for n, score in enumerate((90, 100, 110)):
        _published(user, "linkedin", f"Our team grew this quarter. Update {n}", score, days_ago=10 + n)
    return user


def test_findings_come_from_what_was_posted_and_feed_the_caption_writer():
    user = _brand_with_history()
    result = insights_service.what_works(user["id"])
    assert result["posts"] == 6
    question = next(f for f in result["insights"] if f["value"] == "question")
    assert question["platform"] == "linkedin" and question["lift"] == 1.6

    notes = insights_service.prompt_notes(user["id"], ["linkedin", "instagram"])
    assert list(notes) == ["linkedin"]  # nothing known about Instagram yet
    assert "open with a question get 1.6×" in notes["linkedin"] and "lean towards this" in notes["linkedin"]
    assert "Text-only" not in notes["linkedin"]  # format isn't the caption writer's choice


def test_insights_endpoint(app):
    user = _brand_with_history()
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    body = client.get("/api/insights").get_json()
    assert body["success"] and body["posts"] == 6 and body["insights"][0]["platform"] == "linkedin"
