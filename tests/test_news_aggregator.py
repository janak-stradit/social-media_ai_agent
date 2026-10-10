"""Our own trend source: news feeds -> cleaned, grouped, ranked stories -> topics.
Runs on saved sample feeds and a fake text model - no network, no paid calls."""

from datetime import datetime, timedelta, timezone

import pytest

from services import news_aggregator as na

# Real time: the end-to-end tests go through clean_items' one-week window
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def rfc822(days_ago: float) -> str:
    from email.utils import format_datetime

    return format_datetime(NOW - timedelta(days=days_ago))


def google_news(*entries) -> bytes:
    """entries: (title, publisher, days_ago)"""
    items = "".join(
        f"<item><title>{t} - {p}</title><link>https://news.google.com/rss/articles/{i}</link>"
        f"<pubDate>{rfc822(d)}</pubDate><description>&lt;a href=\"x\"&gt;{t}&lt;/a&gt;</description>"
        f"<source url=\"https://{p.lower().replace(' ', '')}.com\">{p}</source></item>"
        for i, (t, p, d) in enumerate(entries)
    )
    return f"<?xml version='1.0'?><rss><channel>{items}</channel></rss>".encode()


def trade_feed(*entries) -> bytes:
    """entries: (title, description, days_ago)"""
    items = "".join(
        f"<item><title>{t}</title><link>https://trade.example/{i}</link><pubDate>{rfc822(d)}</pubDate>"
        f"<description><![CDATA[<p>{desc}</p>]]></description></item>"
        for i, (t, desc, d) in enumerate(entries)
    )
    return f"<?xml version='1.0'?><rss><channel>{items}</channel></rss>".encode()


GOOGLE = {"name": "Google News", "url": "https://news.google.com/rss/x", "industries": {"retail_ecommerce"},
          "regions": None, "aggregator": True}
TRADE = {"name": "Retail Dive", "url": "https://trade.example/feed", "industries": {"retail_ecommerce"}, "regions": None}
GENERAL = {"name": "Arabian Business", "url": "https://general.example/feed", "industries": None, "regions": None}


# ── Feeds chosen per industry + market ──────────────────────────────────────


def test_feeds_follow_the_industry_and_market():
    us = {f["name"] for f in na.feeds_for("retail_ecommerce", "US", "retail")}
    india = {f["name"] for f in na.feeds_for("retail_ecommerce", "India", "retail")}
    uae = {f["name"] for f in na.feeds_for("retail_ecommerce", "UAE/GCC", "retail")}
    assert {"Google News", "Retail Dive", "Modern Retail"} <= us and "ET Retail" not in us
    assert "ET Retail" in india and "Retail Dive" not in india
    assert "Arabian Business" in uae and "Retail Dive" not in uae  # general press, filtered by keyword later
    # outside the US the search names the market (the UAE edition alone returns world news)
    google_uae = na.feeds_for("retail_ecommerce", "UAE/GCC", "retail")[0]["url"]
    assert "Dubai" in google_uae and "gl=AE" in google_uae
    assert "Dubai" not in na.feeds_for("retail_ecommerce", "US", "retail")[0]["url"]


# ── Parsing and cleaning ────────────────────────────────────────────────────


def test_google_news_items_drop_the_publisher_suffix_and_echoed_description():
    [item] = na.parse_feed(google_news(("Walmart adds 1-hour pickup", "CNBC", 1)), GOOGLE)
    assert item["title"] == "Walmart adds 1-hour pickup" and item["publisher"] == "CNBC"
    assert item["description"] == "" and not item["trade"]


def test_a_publisher_given_as_a_url_shows_as_its_domain():
    feed = (b"<rss><channel><item><title>Dubai off-plan boost</title><link>https://news.google.com/a</link>"
            b"<source url='https://www.nigeriahousingmarket.com'>https://www.nigeriahousingmarket.com/</source></item></channel></rss>")
    [item] = na.parse_feed(feed, GOOGLE)
    assert item["publisher"] == "nigeriahousingmarket.com"


def test_trade_items_keep_their_description_as_plain_text():
    [item] = na.parse_feed(trade_feed(("Gap tests AI shopping", "Gap said <b>shoppers</b> can &amp; will chat.", 1)), TRADE)
    assert item["publisher"] == "Retail Dive" and item["trade"]
    assert item["description"] == "Gap said shoppers can & will chat."


def test_atom_feeds_are_read_too():
    atom = (b"<feed xmlns='http://www.w3.org/2005/Atom'><entry><title>Verge story</title>"
            b"<link href='https://verge.example/a'/><updated>2026-10-08T10:00:00Z</updated>"
            b"<summary>Short summary</summary></entry></feed>")
    [item] = na.parse_feed(atom, {"name": "The Verge", "url": "u", "industries": {"technology_saas"}, "regions": None})
    assert item["url"] == "https://verge.example/a" and item["published"].day == 8


@pytest.mark.parametrize("title, publisher", [
    ("Opinion | Retail is dying", "NYT"),
    ("Live updates: Black Friday sales", "CNN"),
    ("Best laptop deals of the week", "Tom's Guide"),
    ("Acme announces new store format", "PR Newswire"),
    ("Shares of Acme rise 3%", "MarketBeat"),
])
def test_noise_is_dropped(title, publisher):
    item = {"title": title, "publisher": publisher, "description": "", "published": NOW, "general": False}
    assert na.clean_items([item], ["retail"], now=NOW) == []


def test_old_future_duplicate_and_off_topic_general_items_are_dropped():
    base = {"publisher": "X", "description": "", "general": False}
    items = [
        {**base, "title": "Fresh retail story", "published": NOW - timedelta(days=1)},
        {**base, "title": "Fresh retail story", "published": NOW - timedelta(days=2)},  # same headline
        {**base, "title": "Old retail story", "published": NOW - timedelta(days=10)},
        {**base, "title": "Broken future date", "published": NOW + timedelta(days=40)},
        {**base, "title": "Oil prices climb", "published": NOW, "general": True},  # general press, not retail
        {**base, "title": "Dubai e-commerce sales jump", "published": NOW, "general": True},
    ]
    kept = [i["title"] for i in na.clean_items(items, ["retail", "e-commerce"], now=NOW)]
    assert kept == ["Fresh retail story", "Dubai e-commerce sales jump"]


# ── Grouping and ranking ────────────────────────────────────────────────────


def _item(title, publisher, days_ago=1, trade=False, description=""):
    return {"title": title, "url": f"https://x/{publisher}", "publisher": publisher, "published": NOW - timedelta(days=days_ago),
            "description": description, "trade": trade, "general": False}


def test_headlines_about_one_story_are_grouped():
    groups = na.group_stories([
        _item("Amazon cuts 14,000 retail jobs", "Reuters"),
        _item("Amazon to cut 14,000 jobs in retail division", "CNBC"),
        _item("Walmart launches drone delivery in Dallas", "Axios"),
    ])
    assert sorted(len(g) for g in groups) == [1, 2]


def test_search_words_do_not_glue_unrelated_headlines_together():
    """Every headline contains the industry words; they must not make a story."""
    items = [
        _item("Artificial intelligence changes religious pilgrimages", "A"),
        _item("Artificial intelligence and the global economy", "B"),
        _item("A caution about artificial intelligence", "C"),
    ]
    ignore = na.query_words(["artificial intelligence", "software"])
    assert len(na.group_stories(items, ignore)) == 3


def test_a_story_covered_by_many_publishers_ranks_first():
    items = [
        _item("Small local shop opens downtown", "Local Paper", days_ago=0.1),
        _item("Amazon cuts 14,000 retail jobs", "Reuters", days_ago=2),
        _item("Amazon to cut 14,000 retail jobs", "CNBC", days_ago=2),
        _item("Amazon retail jobs cut by 14,000", "WSJ", days_ago=2),
        _item("Gap tests AI shopping assistant", "Retail Dive", days_ago=1, trade=True, description="Gap said..."),
    ]
    stories = na.rank_stories(items, now=NOW)
    assert stories[0]["publishers"] == ["CNBC", "Reuters", "WSJ"]
    assert stories[1]["title"] == "Gap tests AI shopping assistant"  # trade press beats a single local story
    assert stories[1]["description"] == "Gap said..."


# ── Writing the topics ──────────────────────────────────────────────────────


class FakeLLM:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.prompt = result, error, None

    def generate_json(self, system_prompt, user_prompt, **kw):
        self.prompt = user_prompt
        if self.error:
            raise self.error
        return self.result, {"total_tokens": 900, "cost_usd": 0.0012}


STORIES = [
    {"title": "Amazon cuts 14,000 retail jobs", "url": "https://reuters.example/a", "publisher": "Reuters",
     "description": "", "headlines": ["Amazon cuts 14,000 retail jobs", "Amazon to cut 14,000 jobs"],
     "publishers": ["CNBC", "Reuters"], "score": 4.1},
    {"title": "Gap tests AI shopping", "url": "https://retaildive.example/g", "publisher": "Retail Dive",
     "description": "Gap said shoppers can chat with an assistant.", "headlines": ["Gap tests AI shopping"],
     "publishers": ["Retail Dive"], "score": 2.6},
]


def test_the_model_words_topics_but_links_come_from_the_story():
    llm = FakeLLM({"topics": [
        {"story": 1, "topic": "Gap pilots an AI shopping assistant", "summary": "Gap said shoppers can chat.",
         "why_now": "Retailers are testing AI before the holidays."},
        {"story": 1, "topic": "Duplicate pick", "summary": "", "why_now": ""},  # same story twice
        {"story": 7, "topic": "Made-up story", "summary": "", "why_now": ""},  # no such story
        {"story": 0, "topic": "Amazon cuts retail jobs", "summary": "Amazon is cutting 14,000 jobs.", "why_now": "x",
         "source_url": "https://invented.example"},
    ]})
    topics, usage = na.write_topics(STORIES, "Retail & e-commerce", "the United States", llm=llm)
    assert [t["topic"] for t in topics] == ["Gap pilots an AI shopping assistant", "Amazon cuts retail jobs"]
    assert topics[1]["source_url"] == "https://reuters.example/a" and topics[1]["source_title"] == "Reuters"
    assert usage["total_tokens"] == 900
    assert "Publishers (2): CNBC, Reuters" in llm.prompt and "Gap said shoppers can chat" in llm.prompt


def test_without_the_model_the_top_stories_are_used_as_they_are():
    topics = na.plain_topics(STORIES)
    assert topics[0]["topic"] == "Amazon cuts 14,000 retail jobs"
    assert topics[0]["why_now"] == "Covered by 2 publishers in the past week."
    assert topics[1]["summary"] == "Gap said shoppers can chat with an assistant." and topics[1]["why_now"] == ""


# ── End to end, with fake feeds ─────────────────────────────────────────────


@pytest.fixture
def fake_feeds(monkeypatch):
    responses = {
        "news.google.com": google_news(
            ("Amazon cuts 14,000 retail jobs", "Reuters", 1), ("Amazon to cut 14,000 retail jobs", "CNBC", 1),
            ("Opinion | Retail is dying", "NYT", 1), ("Walmart adds 1-hour pickup", "Axios", 2),
        ),
        "retaildive.com": trade_feed(("Gap tests AI shopping", "Gap said shoppers can chat.", 1)),
    }
    calls = []

    class Response:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            if self.content is None:
                raise RuntimeError("HTTP 403")

    def fake_get(url, **kw):
        calls.append(url)
        return Response(next((body for host, body in responses.items() if host in url), None))

    monkeypatch.setattr(na.requests, "get", fake_get)
    monkeypatch.setattr(na, "_cache", {})
    return calls


def test_aggregate_survives_broken_feeds_and_falls_back_without_the_model(fake_feeds, monkeypatch):
    def broken_writer(*a, **kw):
        raise RuntimeError("gateway timeout")

    monkeypatch.setattr(na, "write_topics", broken_writer)
    topics = na.aggregate_trends("retail_ecommerce", "Retail & e-commerce", "US", "retail OR e-commerce", "the US")

    assert topics[0]["topic"] == "Amazon cuts 14,000 retail jobs"  # 2 publishers
    assert "Gap tests AI shopping" in [t["topic"] for t in topics]
    assert not any("Opinion" in t["topic"] for t in topics)
    # Modern Retail's URL isn't in the fake responses -> that feed fails, the rest still count
    assert any("modernretail" in u for u in fake_feeds)


def test_feeds_are_cached_between_lookups(fake_feeds, monkeypatch):
    monkeypatch.setattr(na, "write_topics", lambda *a, **kw: ([], {}))
    na.aggregate_trends("retail_ecommerce", "Retail", "US", "retail", "the US")
    first = len(fake_feeds)
    na.aggregate_trends("retail_ecommerce", "Retail", "US", "retail", "the US")
    successful = sum(1 for u in fake_feeds[:first] if "google" in u or "retaildive" in u)
    assert len(fake_feeds) - first == first - successful  # only the failed feed is fetched again


def test_aggregate_raises_when_no_feed_can_be_read(monkeypatch):
    def offline(url, **kw):
        raise ConnectionError("offline")

    monkeypatch.setattr(na.requests, "get", offline)
    monkeypatch.setattr(na, "_cache", {})
    with pytest.raises(RuntimeError, match="No news feed"):
        na.aggregate_trends("retail_ecommerce", "Retail", "US", "retail", "the US")
