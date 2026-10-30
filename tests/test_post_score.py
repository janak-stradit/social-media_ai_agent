"""Post score (services/post_score_service.py): common-practice checks, made
personal by the brand's own findings, and the endpoint the editor calls."""

import uuid

import pytest

import db
from services.post_score_service import score


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


def _checks(result):
    return {c["title"]: c for c in result["checks"]}


def test_a_well_made_linkedin_post_passes_common_practice():
    caption = "Three questions to ask before you invest this year.\n\n" + "Here is how to think about it. " * 15 + \
        "Speak to an adviser before you decide."
    result = score("linkedin", caption, ["#Investing", "#Tax", "#India"])
    assert result["label"] == "Strong" and result["passed"] == result["total"] == 4 and not result["personal"]


def test_what_needs_work_is_named():
    caption = "Financial planning is important for every family and especially when a new baby arrives because there " \
              "are so many new costs to think about, from insurance to education.\n\nWe help families."
    result = score("linkedin", caption, [f"#t{n}" for n in range(10)])
    checks = _checks(result)
    assert not checks["Opening runs past “…more”"]["ok"]
    assert not checks["No call to action"]["ok"]
    assert not checks["10 hashtags is a lot for LinkedIn"]["ok"]
    assert result["label"] == "Needs work"


def test_other_languages_skip_the_call_to_action_check():
    result = score("instagram", "आज ही अपनी बचत की योजना बनाएं और अपने परिवार का भविष्य सुरक्षित करें।", ["#बचत", "#योजना", "#परिवार"])
    assert not any("call to action" in c["title"].lower() or "next step" in c["title"].lower() for c in result["checks"])


def test_the_brands_own_findings_replace_common_practice():
    findings = [
        {"platform": "linkedin", "trait": "opening", "value": "question", "lift": 1.7, "posts": 5, "others": 5, "text": ""},
        {"platform": "linkedin", "trait": "hashtags", "value": "few", "lift": 1.6, "posts": 4, "others": 6, "text": ""},
        {"platform": "instagram", "trait": "opening", "value": "number", "lift": 2.0, "posts": 4, "others": 4, "text": ""},
    ]
    caption = "Our team grew this quarter.\n\n" + "More about it. " * 30 + "Tell us what you think."
    checks = _checks(score("linkedin", caption, ["#a", "#b", "#c", "#d"], findings=findings))
    assert checks["Try a different opening"]["note"] == "Your LinkedIn posts that open with a question get 1.7× your others."
    assert checks["Hashtag count is off for you"]["note"].endswith("This one has 4.")  # theirs: 0 to 2 does better
    assert not any(t.startswith("Good number of hashtags") for t in checks)  # common practice gives way
    asked = _checks(score("linkedin", "Is your team ready?\n\n" + "More. " * 80, [], findings=findings))
    assert asked["Opening style works for you"]["ok"] and asked["Hashtags work for you"]["ok"]


def test_post_score_endpoint(app):
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")
    client = app.test_client()
    assert client.post("/api/post-score", json={"platform": "facebook", "caption": "Hi"}).status_code == 401
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    body = client.post("/api/post-score", json={"platform": "facebook", "caption": "Join our webinar on Thursday. Register now.",
                                                "hashtags": ["#Webinar"]}).get_json()
    assert body["success"] and body["label"] == "Strong" and body["total"] == 4
