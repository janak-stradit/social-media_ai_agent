"""Covers the dedupe logic behind the competitor dashboard: repeated scans or
LLM runs must not create duplicate rows for the same post/opportunity."""

import db


def _post(competitor="BlackRock", platform="linkedin", post_url="https://linkedin.com/post/1", **overrides):
    payload = {
        "_source_competitor": competitor,
        "platform": platform,
        "post_url": post_url,
        "title": "Test post",
        "text": "Some competitor content.",
    }
    payload.update(overrides)
    return payload


def test_save_competitor_posts_inserts_new_posts():
    result = db.save_competitor_posts([_post()])
    assert result["inserted"] == 1
    assert result["skipped"] == 0
    assert result["new_post_urls"] == ["https://linkedin.com/post/1"]

    stored = db.get_competitor_posts(platform="linkedin")
    assert len(stored) == 1
    assert stored[0]["_source_competitor"] == "BlackRock"


def test_save_competitor_posts_skips_exact_duplicates_on_rescan():
    db.save_competitor_posts([_post()])
    result = db.save_competitor_posts([_post()])

    assert result["inserted"] == 0
    assert result["skipped"] == 1
    assert db.get_competitor_posts(platform="linkedin") and len(db.get_competitor_posts(platform="linkedin")) == 1


def test_save_competitor_posts_treats_different_platform_as_distinct():
    db.save_competitor_posts([_post(platform="linkedin")])
    result = db.save_competitor_posts([_post(platform="twitter", post_url="https://linkedin.com/post/1")])

    assert result["inserted"] == 1
    assert len(db.get_competitor_posts()) == 2


def test_save_competitor_posts_skips_posts_without_url():
    result = db.save_competitor_posts([_post(post_url=None)])
    assert result == {"inserted": 0, "skipped": 1, "new_post_urls": []}


def test_get_competitor_posts_filters_by_competitor():
    db.save_competitor_posts(
        [
            _post(competitor="BlackRock", post_url="https://x.com/1"),
            _post(competitor="Northern Trust", post_url="https://x.com/2"),
        ]
    )

    only_blackrock = db.get_competitor_posts(competitor="BlackRock")
    assert len(only_blackrock) == 1
    assert only_blackrock[0]["_source_competitor"] == "BlackRock"


def test_save_opportunity_suggestions_inserts_and_dedupes_case_insensitively():
    first = db.save_opportunity_suggestions(
        unserved_themes=[{"title": "ESG Reporting Gaps", "description": "desc"}],
        domain_expansion=[{"title": "Custom Integrations", "description": "desc"}],
        source_accounts="BlackRock",
    )
    assert first["inserted"] == 2
    assert set(first["new_titles"]) == {"ESG Reporting Gaps", "Custom Integrations"}

    second = db.save_opportunity_suggestions(
        unserved_themes=[{"title": "esg reporting gaps", "description": "same idea, different case"}],
        domain_expansion=[],
    )
    assert second["inserted"] == 0
    assert second["skipped"] == 1

    suggestions = db.get_opportunity_suggestions()
    assert len(suggestions["unserved_themes"]) == 1
    assert len(suggestions["domain_expansion"]) == 1


def test_save_opportunity_suggestions_skips_blank_titles():
    result = db.save_opportunity_suggestions(
        unserved_themes=[{"title": "  ", "description": "no title"}], domain_expansion=[]
    )
    assert result == {"inserted": 0, "skipped": 1, "new_titles": []}
