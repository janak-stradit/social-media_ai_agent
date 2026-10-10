"""This week's talked-about topics for an industry + market, from open news feeds.

The main trend source for Studio Chat's "Ideas for you" (services/trend_service.py).
No search API and no scraping of search-result pages - only RSS feeds that
publishers offer for exactly this use:

  1. Collect   Google News search for the industry in the market's edition, plus
               the industry's trade publications and the market's business press
               (FEEDS). ~100-250 headlines from the past week.
  2. Clean     Drop opinion pieces, press-release wires, deal listicles, live blogs
               and anything older than a week; general business feeds only keep
               items that mention the industry.
  3. Group     Headlines about the same story are merged (shared significant words).
  4. Rank      A story covered by more publishers ranks higher - that is what
               "talked-about" means - with a bonus for trade press and recency.
  5. Write     One call to the text model turns the top stories into
               {topic, summary, why_now} using only the headlines and feed
               descriptions given. Without the model, the top stories are used as
               they are, so the panel still gets topics.

Feed responses are cached for FEED_CACHE_SECONDS, so the scheduler refreshing
many industries in one market fetches each shared feed once.
"""

import concurrent.futures
import email.utils
import html
import logging
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; AVIRNewsBot/1.0; +https://stradit.com/)"
FETCH_TIMEOUT = 12
MAX_AGE_DAYS = 7
FEED_CACHE_SECONDS = 30 * 60
CANDIDATE_STORIES = 10  # stories offered to the model, which picks the best MAX_TOPICS
MAX_TOPICS = 6

# Google News edition per market (same markets as trend_service.REGIONS)
GOOGLE_NEWS_EDITIONS = {
    "India": "hl=en-IN&gl=IN&ceid=IN:en",
    "US": "hl=en-US&gl=US&ceid=US:en",
    "UAE/GCC": "hl=en-AE&gl=AE&ceid=AE:en",
}
# A market's edition still returns world news (Texas restaurant awards in the
# UAE edition), so outside the US the search must also name the market.
# Global trade feeds keep worldwide industry news in the mix.
MARKET_TERMS = {
    "India": "India",
    "UAE/GCC": 'UAE OR Dubai OR "Abu Dhabi" OR Sharjah OR Saudi OR Qatar OR GCC',
}

# Publisher feeds. industries: compliance_rules.INDUSTRIES keys the feed covers,
# or None for a general business feed (kept only for items naming the industry).
# regions: markets the feed is shown for, or None for all.
# Checked live 2026-10-09; feeds that block bots or moved are left out.
FEEDS = [
    # Retail & e-commerce
    {"name": "Retail Dive", "url": "https://www.retaildive.com/feeds/news/", "industries": {"retail_ecommerce"}, "regions": {"US"}},
    {"name": "Modern Retail", "url": "https://www.modernretail.co/feed/", "industries": {"retail_ecommerce"}, "regions": {"US"}},
    {"name": "ET Retail", "url": "https://retail.economictimes.indiatimes.com/rss/topstories", "industries": {"retail_ecommerce"}, "regions": {"India"}},
    # Technology & SaaS
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/", "industries": {"technology_saas"}, "regions": None},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml", "industries": {"technology_saas"}, "regions": {"US"}},
    {"name": "ET Tech", "url": "https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms", "industries": {"technology_saas"}, "regions": {"India"}},
    {"name": "ET CIO", "url": "https://cio.economictimes.indiatimes.com/rss/topstories", "industries": {"technology_saas"}, "regions": {"India"}},
    # Healthcare, pharma & medical devices
    {"name": "Healthcare Dive", "url": "https://www.healthcaredive.com/feeds/news/", "industries": {"healthcare"}, "regions": {"US"}},
    {"name": "Fierce Healthcare", "url": "https://www.fiercehealthcare.com/rss/xml", "industries": {"healthcare"}, "regions": {"US"}},
    {"name": "ET HealthWorld", "url": "https://health.economictimes.indiatimes.com/rss/topstories", "industries": {"healthcare", "pharma_medical_devices"}, "regions": {"India"}},
    {"name": "BioPharma Dive", "url": "https://www.biopharmadive.com/feeds/news/", "industries": {"pharma_medical_devices"}, "regions": None},
    {"name": "MedTech Dive", "url": "https://www.medtechdive.com/feeds/news/", "industries": {"pharma_medical_devices"}, "regions": None},
    # Finance, investing, insurance, crypto
    {"name": "Banking Dive", "url": "https://www.bankingdive.com/feeds/news/", "industries": {"financial_services"}, "regions": {"US"}},
    {"name": "Payments Dive", "url": "https://www.paymentsdive.com/feeds/news/", "industries": {"financial_services"}, "regions": {"US"}},
    {"name": "Finextra", "url": "https://www.finextra.com/rss/headlines.aspx", "industries": {"financial_services"}, "regions": None},
    {"name": "ET BFSI", "url": "https://bfsi.economictimes.indiatimes.com/rss/topstories", "industries": {"financial_services", "insurance"}, "regions": {"India"}},
    {"name": "ET Markets", "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "industries": {"investment_wealth"}, "regions": {"India"}},
    {"name": "Mint Markets", "url": "https://www.livemint.com/rss/markets", "industries": {"investment_wealth"}, "regions": {"India"}},
    {"name": "Insurance Journal", "url": "https://www.insurancejournal.com/rss/news/", "industries": {"insurance"}, "regions": {"US"}},
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "industries": {"crypto_digital_assets"}, "regions": None},
    {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss", "industries": {"crypto_digital_assets"}, "regions": None},
    # Legal, real estate
    {"name": "Above the Law", "url": "https://abovethelaw.com/feed/", "industries": {"legal_services"}, "regions": {"US"}},
    {"name": "ET Legal", "url": "https://legal.economictimes.indiatimes.com/rss/topstories", "industries": {"legal_services"}, "regions": {"India"}},
    {"name": "HousingWire", "url": "https://www.housingwire.com/feed/", "industries": {"real_estate"}, "regions": {"US"}},
    {"name": "ET Realty", "url": "https://realty.economictimes.indiatimes.com/rss/topstories", "industries": {"real_estate"}, "regions": {"India"}},
    # Food, education, hiring
    {"name": "Food Dive", "url": "https://www.fooddive.com/feeds/news/", "industries": {"supplements_food"}, "regions": None},
    {"name": "K-12 Dive", "url": "https://www.k12dive.com/feeds/news/", "industries": {"education"}, "regions": {"US"}},
    {"name": "Higher Ed Dive", "url": "https://www.highereddive.com/feeds/news/", "industries": {"education"}, "regions": {"US"}},
    {"name": "EdSurge", "url": "https://www.edsurge.com/articles_rss", "industries": {"education"}, "regions": None},
    {"name": "ET Education", "url": "https://education.economictimes.indiatimes.com/rss/topstories", "industries": {"education"}, "regions": {"India"}},
    {"name": "HR Dive", "url": "https://www.hrdive.com/feeds/news/", "industries": {"employment_recruiting"}, "regions": {"US"}},
    {"name": "ET HRWorld", "url": "https://hr.economictimes.indiatimes.com/rss/topstories", "industries": {"employment_recruiting"}, "regions": {"India"}},
    {"name": "ET Jobs", "url": "https://economictimes.indiatimes.com/jobs/rssfeeds/107115.cms", "industries": {"employment_recruiting"}, "regions": {"India"}},
    # Logistics, travel, manufacturing, services, nonprofit
    {"name": "Supply Chain Dive", "url": "https://www.supplychaindive.com/feeds/news/", "industries": {"logistics_transport"}, "regions": None},
    {"name": "FreightWaves", "url": "https://www.freightwaves.com/news/feed", "industries": {"logistics_transport"}, "regions": {"US"}},
    {"name": "ET Infra", "url": "https://infra.economictimes.indiatimes.com/rss/topstories", "industries": {"logistics_transport"}, "regions": {"India"}},
    {"name": "Skift", "url": "https://skift.com/feed/", "industries": {"hospitality_travel"}, "regions": None},
    {"name": "Restaurant Dive", "url": "https://www.restaurantdive.com/feeds/news/", "industries": {"hospitality_travel"}, "regions": {"US"}},
    {"name": "Hotel Dive", "url": "https://www.hoteldive.com/feeds/news/", "industries": {"hospitality_travel"}, "regions": {"US"}},
    {"name": "ET TravelWorld", "url": "https://travel.economictimes.indiatimes.com/rss/topstories", "industries": {"hospitality_travel"}, "regions": {"India"}},
    {"name": "Manufacturing Dive", "url": "https://www.manufacturingdive.com/feeds/news/", "industries": {"manufacturing_industrial"}, "regions": None},
    {"name": "CFO Dive", "url": "https://www.cfodive.com/feeds/news/", "industries": {"professional_services"}, "regions": {"US"}},
    {"name": "Nonprofit Quarterly", "url": "https://nonprofitquarterly.org/feed/", "industries": {"nonprofit"}, "regions": {"US"}},
    # General business press per market (filtered to the industry's keywords)
    {"name": "ET Industry", "url": "https://economictimes.indiatimes.com/industry/rssfeeds/13352306.cms", "industries": None, "regions": {"India"}},
    {"name": "Arabian Business", "url": "https://www.arabianbusiness.com/feed", "industries": None, "regions": {"UAE/GCC"}},
    {"name": "The National", "url": "https://www.thenationalnews.com/arc/outboundfeeds/rss/category/business/?outputType=xml", "industries": None, "regions": {"UAE/GCC"}},
    {"name": "Khaleej Times", "url": "https://www.khaleejtimes.com/stories.rss?section=business", "industries": None, "regions": {"UAE/GCC"}},
]

# Not news: opinion, live blogs, listicles, sponsored content
_NOISE_TITLE = re.compile(
    r"^(opinion|op-ed|editorial|letters?( to the editor)?|podcast|video|watch|listen|live( updates| blog)?|quiz|"
    r"sponsored|partner content|advertorial|press release)\b|\|\s*opinion\b|\bopinion\s*\||\(opinion\)|"
    r"\b(promo codes?|coupon codes?|best .* deals|deals of the day|horoscope)\b",
    re.IGNORECASE,
)
# Press-release wires and auto-generated stock pages
_NOISE_PUBLISHERS = {
    "pr newswire", "prnewswire", "globenewswire", "business wire", "businesswire", "ein presswire", "openpr",
    "access newswire", "accesswire", "newsfile", "marketbeat", "stock titan", "simply wall st", "newswire",
}
_STOPWORDS = set(
    "the a an and or of to in on for with at by from as is are was were be been it its this that these those "  # noqa: SIM905
    "after amid over under into about than then new news says said say report reports reported how why what when "
    "who will would could may might can its their his her our your more most up out off not no yes vs via "
    "year years week weeks today day days first last next amp inc ltd co company companies plans plan set sets".split()
)

_cache: dict[str, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()


# ── 1. Collect ──────────────────────────────────────────────────────────────


def industry_keywords(query: str) -> list[str]:
    """'retail OR e-commerce OR online shopping' -> ['retail', 'e-commerce', 'online shopping']."""
    return [k.strip().lower() for k in re.split(r"\bOR\b", query or "") if k.strip()]


def feeds_for(category: str, region: str, query: str) -> list[dict]:
    """The feeds to read for an industry + market: Google News first, then
    trade publications, then the market's general business press."""
    edition = GOOGLE_NEWS_EDITIONS.get(region, GOOGLE_NEWS_EDITIONS["US"])
    market = f" ({MARKET_TERMS[region]})" if region in MARKET_TERMS else ""
    search = urllib.parse.quote(f"({query}){market} when:{MAX_AGE_DAYS}d")
    chosen = [{"name": "Google News", "url": f"https://news.google.com/rss/search?q={search}&{edition}",
               "industries": {category}, "regions": None, "aggregator": True}]
    for feed in FEEDS:
        if feed["regions"] is not None and region not in feed["regions"]:
            continue
        if feed["industries"] is None or category in feed["industries"]:
            chosen.append(feed)
    return chosen


def _text(element, *tags) -> str:
    for tag in tags:
        value = element.findtext(tag)
        if value and value.strip():
            return value.strip()
    return ""


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _publisher_name(name: str) -> str:
    """Some Google News sources carry a URL instead of a name: show the domain."""
    name = (name or "").strip()
    if name.startswith(("http://", "https://", "www.")):
        name = re.sub(r"^(https?://)?(www\.)?", "", name).split("/")[0]
    return name


def _parse_date(text: str) -> datetime | None:
    if not text:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_feed(content: bytes, feed: dict) -> list[dict]:
    """RSS 2.0 or Atom -> [{title, url, publisher, published, description, feed, trade}]."""
    atom = "{http://www.w3.org/2005/Atom}"
    root = ET.fromstring(content)
    entries = root.findall(".//item") or root.findall(f".//{atom}entry")
    items = []
    for entry in entries:
        title = _clean_html(_text(entry, "title", f"{atom}title"))
        link = _text(entry, "link")
        if not link:
            node = entry.find(f"{atom}link")
            link = node.get("href", "") if node is not None else ""
        publisher = _publisher_name(_text(entry, "source")) if feed.get("aggregator") else feed["name"]
        # Google News titles end with " - Publisher"
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3].strip()
        description = _clean_html(_text(entry, "description", f"{atom}summary", f"{atom}content"))
        if feed.get("aggregator") or description.lower().startswith(title.lower()[:40]):
            description = ""  # Google News descriptions only repeat the headline
        published = _parse_date(_text(entry, "pubDate", f"{atom}published", f"{atom}updated",
                                      "{http://purl.org/dc/elements/1.1/}date"))
        if title and link.startswith(("http://", "https://")):
            items.append({
                "title": title[:220], "url": link, "publisher": (publisher or feed["name"])[:80],
                "published": published, "description": description[:600], "feed": feed["name"],
                "trade": not feed.get("aggregator") and feed.get("industries") is not None,
                "general": feed.get("industries") is None,
            })
    return items


def fetch_feed(feed: dict) -> list[dict]:
    """A feed's items, from the short-lived cache when fresh. Raises on failure."""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(feed["url"])
        if hit and now - hit[0] < FEED_CACHE_SECONDS:
            return hit[1]
    response = requests.get(feed["url"], timeout=FETCH_TIMEOUT, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    items = parse_feed(response.content, feed)
    with _cache_lock:
        _cache[feed["url"]] = (now, items)
    return items


# ── 2. Clean ────────────────────────────────────────────────────────────────


def is_noise(item: dict) -> bool:
    publisher = item["publisher"].lower()
    return bool(_NOISE_TITLE.search(item["title"])) or any(p in publisher for p in _NOISE_PUBLISHERS)


def mentions_industry(item: dict, keywords: list[str]) -> bool:
    text = f"{item['title']} {item['description']}".lower()
    return any(re.search(rf"(?<![a-z]){re.escape(k)}", text) for k in keywords)


def clean_items(items: list[dict], keywords: list[str], now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    oldest, newest = now - timedelta(days=MAX_AGE_DAYS), now + timedelta(days=1)
    kept, seen = [], set()
    for item in items:
        published = item["published"]
        if published and not oldest <= published <= newest:
            continue  # older than a week, or a broken future date
        if is_noise(item) or (item["general"] and not mentions_industry(item, keywords)):
            continue
        key = re.sub(r"[^a-z0-9]", "", item["title"].lower())
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    return kept


# ── 3-4. Group and rank ─────────────────────────────────────────────────────


def query_words(keywords: list[str]) -> set[str]:
    """The words of the industry search ('artificial', 'intelligence', ...):
    every headline has them, so they say nothing about which story it is."""
    return {w for k in keywords for w in re.findall(r"[a-z0-9]+", k.lower())}


def _tokens(title: str, ignore: set[str] = frozenset()) -> set[str]:
    words = re.findall(r"[a-z0-9][a-z0-9'&.-]*", title.lower())
    out = set()
    for word in words:
        word = word.strip("'.-")
        if len(word) < 3 or word in _STOPWORDS or word in ignore or word.isdigit():
            continue
        out.add(word[:-1] if len(word) > 4 and word.endswith("s") and not word.endswith("ss") else word)
    return out


def same_story(a: set[str], b: set[str]) -> bool:
    shared = len(a & b)
    return shared >= 2 and shared / max(1, min(len(a), len(b))) >= 0.5


def group_stories(items: list[dict], ignore: set[str] = frozenset()) -> list[list[dict]]:
    """A headline joins the first story whose lead headline it shares enough
    words with. Comparing with the lead (not every member) stops chains where
    A~B and B~C pull an unrelated C into A's story."""
    stories: list[tuple[set[str], list[dict]]] = []
    for item in items:
        tokens = _tokens(item["title"], ignore)
        if not tokens:
            continue
        for lead_tokens, members in stories:
            if same_story(tokens, lead_tokens):
                members.append(item)
                break
        else:
            stories.append((tokens, [item]))
    return [members for _, members in stories]


def score_story(members: list[dict], now: datetime | None = None) -> float:
    """Distinct publishers (the 'talked-about' signal), + trade press, + recency."""
    now = now or datetime.now(timezone.utc)
    publishers = {m["publisher"].lower() for m in members}
    dates = [m["published"] for m in members if m["published"]]
    age_days = (now - max(dates)).total_seconds() / 86400 if dates else MAX_AGE_DAYS / 2
    recency = max(0.0, 1 - age_days / MAX_AGE_DAYS)  # 1 today .. 0 a week ago
    trade = 0.75 if any(m["trade"] for m in members) else 0.0
    return len(publishers) + trade + recency


def rank_stories(items: list[dict], now: datetime | None = None, ignore: set[str] = frozenset()) -> list[dict]:
    stories = []
    for members in group_stories(items, ignore):
        # The headline shown: trade press first (usually the clearest), then the newest
        lead = min(members, key=lambda m: (not m["trade"], -(m["published"].timestamp() if m["published"] else 0)))
        description = next((m["description"] for m in [lead] + members if m["description"]), "")
        stories.append({
            "title": lead["title"], "url": lead["url"], "publisher": lead["publisher"], "description": description,
            "headlines": [m["title"] for m in members][:5],
            "publishers": sorted({m["publisher"] for m in members}),
            "score": round(score_story(members, now), 3),
        })
    stories.sort(key=lambda s: s["score"], reverse=True)
    return stories


# ── 5. Write ────────────────────────────────────────────────────────────────

WRITER_SYSTEM_PROMPT = """You pick this week's most talked-about news topics for companies in one industry and market,
for planning social media posts. You get numbered news stories (headlines from several publishers and a short
description). Choose the {count} stories most relevant to companies in this industry and market - skip local
crime, celebrity, sports and politics unless it clearly affects the industry, and skip duplicates.

Return ONLY this JSON:
{{"topics": [{{"story": 0, "topic": "", "summary": "", "why_now": ""}}]}}

- story: the story's number.
- topic: a short, neutral headline, at most 12 words.
- summary: two sentences on what happened, using ONLY facts in that story's headlines and description. Never add
  numbers, names, quotes or claims that are not there.
- why_now: one sentence on why it matters this week to companies in the industry.
Order the topics from most to least talked-about."""


def write_topics(stories: list[dict], label: str, region_name: str, llm=None) -> tuple[list[dict], dict]:
    """(topics, usage). The model picks and words the topics; the source link
    always comes from the story itself, never from the model."""
    from services.llm_service import LLMService

    candidates = stories[:CANDIDATE_STORIES]
    lines = []
    for i, story in enumerate(candidates):
        lines.append(
            f"[{i}] Publishers ({len(story['publishers'])}): {', '.join(story['publishers'][:6])}\n"
            + "\n".join(f"    - {h}" for h in story["headlines"])
            + (f"\n    Description: {story['description'][:400]}" if story["description"] else "")
        )
    user_prompt = f"INDUSTRY: {label}\nMARKET: {region_name}\n\nSTORIES:\n" + "\n\n".join(lines)
    # Streamed (on_partial): the gateway takes ~45 s before the first word, and
    # a non-streamed call that long hits the client timeout and is retried.
    result, usage = (llm or LLMService()).generate_json(
        WRITER_SYSTEM_PROMPT.format(count=MAX_TOPICS), user_prompt,
        temperature=0.3, max_tokens=1800, return_usage=True, reasoning_effort="low", on_partial=lambda _text: None,
    )
    topics, used = [], set()
    for entry in (result or {}).get("topics") or []:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("story"))
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(candidates) or index in used or not str(entry.get("topic") or "").strip():
            continue
        used.add(index)
        story = candidates[index]
        topics.append({
            "topic": str(entry["topic"]).strip(), "summary": str(entry.get("summary") or "").strip(),
            "why_now": str(entry.get("why_now") or "").strip(),
            "source_title": story["publisher"], "source_url": story["url"],
        })
    return topics[:MAX_TOPICS], usage or {}


def plain_topics(stories: list[dict]) -> list[dict]:
    """The top stories as topics, without the model."""
    topics = []
    for story in stories[:MAX_TOPICS]:
        count = len(story["publishers"])
        topics.append({
            "topic": story["title"], "summary": story["description"][:300],
            "why_now": f"Covered by {count} publishers in the past week." if count > 1 else "",
            "source_title": story["publisher"], "source_url": story["url"],
        })
    return topics


# ── Entry point ─────────────────────────────────────────────────────────────


def aggregate_trends(category: str, label: str, region: str, query: str, region_name: str,
                     use_llm: bool = True) -> list[dict]:
    """Up to MAX_TOPICS topics ({topic, summary, why_now, source_title,
    source_url}) for the industry + market. Raises when no feed could be read."""
    from services.observability import log_event

    started = time.monotonic()
    feeds = feeds_for(category, region, query)
    items, failed = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(feeds))) as pool:
        futures = {pool.submit(fetch_feed, feed): feed for feed in feeds}
        for future in concurrent.futures.as_completed(futures):
            try:
                items.extend(future.result())
            except Exception as err:  # noqa: BLE001 - one broken feed must not sink the lookup
                failed.append(futures[future]["name"])
                logger.warning(f"News feed {futures[future]['name']} failed: {str(err)[:160]}")
    if len(failed) == len(feeds):
        raise RuntimeError("No news feed could be read.")

    keywords = industry_keywords(query)
    kept = clean_items(items, keywords)
    stories = rank_stories(kept, ignore=query_words(keywords))
    topics, usage, writer = [], {}, "plain"
    if stories and use_llm:
        try:
            topics, usage = write_topics(stories, label, region_name)
            writer = "llm"
        except Exception as err:  # noqa: BLE001 - the plain stories still serve the user
            logger.warning(f"Trend writer failed, using headlines as they are: {str(err)[:200]}")
    if not topics:
        topics, writer = plain_topics(stories), "plain"

    log_event(
        "trends.aggregated", key=f"{category}|{region}", feeds=len(feeds), feeds_failed=len(failed) or None,
        items=len(items), kept=len(kept), stories=len(stories), topics=len(topics), writer=writer,
        tokens=(usage or {}).get("total_tokens"), cost_usd=(usage or {}).get("cost_usd"),
        ms=int((time.monotonic() - started) * 1000),
    )
    return topics
