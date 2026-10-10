"""Studio Chat's "Ideas for you": what a user could post about right now.

Three groups, written for the user's own brand:
  trending  - this week's talked-about topics in their industry and market
  playbook  - post types that work for their industry (no external data)
  dates     - upcoming occasions in their markets

Trends are looked up once per industry + market and cached (db.IndustryTrend),
so fifty users in one industry share one lookup. They come from Gemini with
Google Search; when that call fails (no key, or the key's plan has no search
grounding) from Google News headlines, so the panel still has something timely.
The finished feed is cached per user (db.UserIdeaFeed) until the next day or
until the industry's trends are refreshed.
"""

import json
import logging
import re
import threading
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

from config import Config
from services.compliance_rules import INDUSTRIES, normalize_industry

logger = logging.getLogger(__name__)

FORMATS = ("text", "image", "video")
PLATFORMS = ("linkedin", "instagram", "facebook", "youtube")
MAX_TRENDS = 6

# What to search the news for, per industry (compliance_rules.INDUSTRIES keys)
TREND_QUERIES = {
    "healthcare": "healthcare OR hospitals OR patient care",
    "pharma_medical_devices": "pharma OR medical devices OR drug approval",
    "financial_services": "banking OR fintech OR digital payments OR lending",
    "investment_wealth": "stock market OR mutual funds OR investing OR wealth management",
    "insurance": "insurance industry OR health insurance OR life insurance",
    "crypto_digital_assets": "crypto OR bitcoin OR digital assets regulation",
    "legal_services": "law firms OR legal industry OR court ruling business",
    "real_estate": "real estate OR housing market OR property prices",
    "alcohol": "alcohol industry OR spirits OR beer market",
    "gambling_gaming": "online gaming OR betting industry regulation",
    "supplements_food": "food industry OR nutrition OR dietary supplements",
    "education": "education OR edtech OR online learning",
    "kids_products": "toys OR kids products OR parenting",
    "employment_recruiting": "hiring OR job market OR recruitment",
    "technology_saas": "software OR SaaS OR artificial intelligence OR cloud",
    "retail_ecommerce": "retail OR e-commerce OR online shopping",
    "logistics_transport": "logistics OR supply chain OR freight",
    "professional_services": "consulting OR business services OR small business",
    "hospitality_travel": "travel OR hotels OR tourism OR restaurants",
    "manufacturing_industrial": "manufacturing OR industrial production OR factories",
    "nonprofit": "non-profit OR charity OR social impact",
    "general": "business news OR small business",
}

# Market (compliance_rules.REGIONS) -> (name used in prompts, Google News edition)
REGIONS = {
    "India": ("India", "hl=en-IN&gl=IN&ceid=IN:en"),
    "US": ("the United States", "hl=en-US&gl=US&ceid=US:en"),
    "UAE/GCC": ("the UAE and the Gulf", "hl=en-AE&gl=AE&ceid=AE:en"),
}
DEFAULT_REGION = "US"

# Post types that work, whatever the week's news. Every industry gets the
# common ones plus its own.
_COMMON_POST_TYPES = [
    "Explainer: one concept your audience keeps asking about, made simple",
    "Myth vs fact: correct one common misunderstanding",
    "Behind the scenes: how your team or product actually works",
    "Customer story: a problem, what changed, the outcome (no invented numbers)",
    "Quick tip: one practical thing the audience can do today",
]
PLAYBOOK = {
    "healthcare": ["Prevention reminder tied to the season", "Meet the specialist", "What to expect at a visit or procedure"],
    "pharma_medical_devices": ["Disease-awareness education", "How the technology works", "Milestone: research, approval or partnership"],
    "financial_services": ["How a fee, rate or process really works", "Fraud and security awareness", "Product walkthrough for one use case"],
    "investment_wealth": ["Market concept explained with a chart", "Risk-management habit", "Tool walkthrough: how to read one dashboard view"],
    "insurance": ["Policy term decoded", "Claim process step by step", "Life-stage checklist"],
    "crypto_digital_assets": ["Regulation update explained", "Security practice for holders", "How one feature of the technology works"],
    "legal_services": ["What a recent ruling means in plain words", "Checklist before signing", "Common mistake and how to avoid it"],
    "real_estate": ["Neighbourhood or market snapshot", "Buyer or renter checklist", "Property spotlight with honest details"],
    "supplements_food": ["Ingredient spotlight", "Recipe or serving idea", "Sourcing and quality story"],
    "education": ["Learner success story", "Study or career tip", "Course or programme preview"],
    "employment_recruiting": ["Role spotlight", "Interview or CV tip", "Hiring trend in one chart"],
    "technology_saas": ["Feature in 30 seconds", "Before and after workflow", "Lesson from building the product"],
    "retail_ecommerce": ["Product in use", "Styling or pairing guide", "New arrival or restock"],
    "logistics_transport": ["Route or delivery story", "Operations tip for shippers", "Fleet or technology spotlight"],
    "professional_services": ["Framework you use with clients", "Lesson from a project", "Question clients should ask before hiring"],
    "hospitality_travel": ["Local guide", "Signature dish or experience", "Seasonal offer"],
    "manufacturing_industrial": ["How it's made", "Quality or safety practice", "Application story from the field"],
    "nonprofit": ["Impact story", "Where donations go", "Volunteer spotlight"],
}

IDEA_FEED_SYSTEM_PROMPT = """You plan social media content for one company. From its brand profile, this week's
trending topics in its industry, post types that work for its industry and upcoming occasions, write post ideas
the company could publish now.

Return ONLY this JSON:
{{"trending": [{{"trend_index": 0, "title": "", "why_now": "", "summary": "", "format": "", "platform": "", "prompt": ""}}],
 "playbook": [{{"post_type": "", "title": "", "why_now": "", "summary": "", "format": "", "platform": "", "prompt": ""}}],
 "dates": [{{"occasion": "", "title": "", "why_now": "", "summary": "", "format": "", "platform": "", "prompt": ""}}]}}

Rules:
- trending: up to {trending_count} ideas, each built on a DIFFERENT topic from TRENDING TOPICS (trend_index is that
  topic's number). Connect the topic to what this company does; skip topics with no honest connection. Use only the
  facts given for the topic - never add numbers, quotes or claims of your own. Empty list when there are no topics.
- playbook: exactly {playbook_count} ideas, each for a different entry of POST TYPES (post_type is that entry's
  first words), made specific to this company's products and audience.
- dates: one idea per occasion in UPCOMING OCCASIONS (at most {dates_count}); empty list when there are none.
- title: at most 8 words. why_now: one short sentence on why this is worth posting this week.
  summary: one sentence describing the post.
- format: "text", "image" or "video" - what suits the idea best. platform: one of "linkedin", "instagram",
  "facebook", "youtube" - where this company's audience would see it.
- prompt: the brief the user will send to the content generator, 2-4 sentences, written as an instruction
  ("Create a post about ..."), naming the angle, the audience and the key points to cover.
- Follow the brand's dos and don'ts and compliance context. For regulated industries never promise results,
  returns or outcomes, and never give individual advice.
- Do not repeat ALREADY SHOWN titles or RECENTLY POSTED topics."""


# ── Industry + market of a brand profile ─────────────────────────────────────


def profile_market(profile: dict) -> tuple[str, str, str]:
    """(industry_category, industry label for prompts, region) of a brand profile."""
    category = normalize_industry(profile.get("industry_category") or profile.get("industry_category_detected"))
    label = (profile.get("industry") or "").strip() or INDUSTRIES.get(category, INDUSTRIES["general"])
    regions = profile.get("compliance_regions") or profile.get("regions_detected") or []
    region = next((r for r in regions if r in REGIONS), DEFAULT_REGION)
    return category, label, region


def trend_key(category: str, region: str) -> str:
    return f"{category}|{region}"


# ── Trend lookup ─────────────────────────────────────────────────────────────


def _clean_topics(items, limit: int = MAX_TRENDS) -> list[dict]:
    topics = []
    for item in items or []:
        if not isinstance(item, dict) or not str(item.get("topic") or "").strip():
            continue
        url = str(item.get("source_url") or "").strip()
        topics.append(
            {
                "topic": str(item["topic"]).strip()[:160],
                "summary": str(item.get("summary") or "").strip()[:500],
                "why_now": str(item.get("why_now") or "").strip()[:240],
                "source_title": str(item.get("source_title") or item.get("source") or "").strip()[:80],
                "source_url": url if url.startswith(("http://", "https://")) else "",
            }
        )
    return topics[:limit]


def _trends_from_google_search(label: str, region: str) -> list[dict]:
    """This week's topics from Gemini with Google Search. Raises when the
    key is missing or the call fails (e.g. no search grounding on the plan)."""
    if not Config.GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not configured.")
    import google.genai as genai
    from google.genai import types

    region_name = REGIONS.get(region, REGIONS[DEFAULT_REGION])[0]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt = (
        f'Use Google Search. Today is {today}. What are the {MAX_TRENDS} most talked-about topics of the past week in '
        f'the "{label}" industry in {region_name} that a company in this industry could post about on social media? '
        "Prefer news, regulation changes, launches, data releases and public conversations. "
        'Reply with ONLY a JSON array, no markdown: [{"topic": "short headline", "summary": "two sentences on what '
        'happened, with the concrete facts", "why_now": "one sentence on why it matters this week", '
        '"source": "publisher name or domain"}]'
    )
    client = genai.Client(api_key=Config.GOOGLE_API_KEY, http_options=types.HttpOptions(timeout=90_000))
    response = client.models.generate_content(
        model=Config.TRENDS_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0.3),
    )
    match = re.search(r"\[.*\]", response.text or "", re.DOTALL)
    if not match:
        raise RuntimeError("Gemini returned no topic list.")
    items = json.loads(match.group(0))

    # The pages Google actually read: used as each topic's link when its publisher matches
    metadata = response.candidates[0].grounding_metadata if response.candidates else None
    pages = [c.web for c in (getattr(metadata, "grounding_chunks", None) or []) if getattr(c, "web", None)]
    for item in items:
        if not isinstance(item, dict):
            continue
        publisher = re.sub(r"[^a-z0-9]", "", str(item.get("source") or "").lower())
        for page in pages:
            page_name = re.sub(r"[^a-z0-9]", "", (page.title or "").lower().removesuffix(".com"))
            if publisher and page_name and (page_name in publisher or publisher in page_name):
                item["source_url"] = page.uri
                break
    topics = _clean_topics(items)
    if not topics:
        raise RuntimeError("Gemini returned an empty topic list.")
    return topics


def _trends_from_google_news(category: str, label: str, region: str) -> list[dict]:
    """Fallback: the past week's headlines for the industry from Google News."""
    query = TREND_QUERIES.get(category) or label
    edition = REGIONS.get(region, REGIONS[DEFAULT_REGION])[1]
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(f'({query}) when:7d')}&{edition}"
    response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    items, seen = [], set()
    for entry in ET.fromstring(response.content).findall("./channel/item"):
        source = (entry.findtext("source") or "").strip()
        title = (entry.findtext("title") or "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        items.append({"topic": title, "source_title": source, "source_url": (entry.findtext("link") or "").strip()})
        if len(items) == MAX_TRENDS:
            break
    return _clean_topics(items)


def get_trends(category: str, label: str, region: str, force: bool = False) -> dict:
    """{"topics", "source", "fetched_at"} for the industry + market, from the
    cache while it is fresh, else looked up now. A failed lookup returns the
    stale cache (or no topics) rather than raising."""
    from db import get_industry_trends, save_industry_trends

    key = trend_key(category, region)
    cached = get_industry_trends(key)
    max_age = timedelta(hours=Config.TRENDS_MAX_AGE_HOURS)
    if cached and not force and datetime.now(timezone.utc) - cached["fetched_at"] < max_age:
        return cached

    for source, lookup in (
        ("google_search", lambda: _trends_from_google_search(label, region)),
        ("google_news", lambda: _trends_from_google_news(category, label, region)),
    ):
        try:
            topics = lookup()
            if topics:
                save_industry_trends(key, topics, source)
                return get_industry_trends(key)
        except Exception as err:  # noqa: BLE001 - the next source, or the stale cache, still serves the user
            logger.warning(f"Trend lookup via {source} failed for {key}: {str(err)[:200]}")
    return cached or {"topics": [], "source": None, "fetched_at": None}


def refresh_stale_trends() -> int:
    """Scheduler: look up again every cached industry whose trends are older
    than TRENDS_MAX_AGE_HOURS, so the first user of the day doesn't wait."""
    from db import list_industry_trend_keys

    refreshed = 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=Config.TRENDS_MAX_AGE_HOURS)
    for key, fetched_at in list_industry_trend_keys():
        if fetched_at and fetched_at >= cutoff:
            continue
        category, _, region = key.partition("|")
        get_trends(category, INDUSTRIES.get(category, INDUSTRIES["general"]), region or DEFAULT_REGION, force=True)
        refreshed += 1
    return refreshed


# ── The user's idea feed ─────────────────────────────────────────────────────

_feed_locks: dict[int, threading.Lock] = {}
_feed_locks_guard = threading.Lock()


def _clean_idea(idea, extra: dict | None = None) -> dict | None:
    if not isinstance(idea, dict) or not str(idea.get("prompt") or "").strip() or not str(idea.get("title") or "").strip():
        return None
    fmt = str(idea.get("format") or "").lower()
    platform = str(idea.get("platform") or "").lower()
    return {
        "title": str(idea["title"]).strip()[:90],
        "why_now": str(idea.get("why_now") or "").strip()[:220],
        "summary": str(idea.get("summary") or "").strip()[:260],
        "format": fmt if fmt in FORMATS else "text",
        "platform": platform if platform in PLATFORMS else "linkedin",
        "prompt": str(idea["prompt"]).strip()[:900],
        **(extra or {}),
    }


def cached_idea_feed(user_id: int) -> dict | None:
    """The user's saved feed while it is still current, else None."""
    from db import get_industry_trends, get_user_brand_profile, get_user_idea_feed

    saved = get_user_idea_feed(user_id)
    profile = get_user_brand_profile(user_id)
    if not saved or not profile:
        return None
    if datetime.now(timezone.utc) - saved["generated_at"] >= timedelta(hours=Config.TRENDS_MAX_AGE_HOURS):
        return None
    category, _, region = profile_market(profile)
    trends = get_industry_trends(trend_key(category, region))
    # The industry got new trends (or the user changed industry) since the feed was written
    if saved["feed"].get("trend_key") != trend_key(category, region):
        return None
    if trends and trends["fetched_at"] and trends["fetched_at"] > saved["generated_at"]:
        return None
    return saved["feed"]


def generate_idea_feed(user_id: int, force: bool = False) -> tuple[dict | None, dict]:
    """(feed, llm usage). feed is None when the user has no brand profile.
    Served from the per-user cache unless it is out of date or `force`."""
    from db import get_history, get_user_brand_profile, save_user_idea_feed
    from services.brand_profile_service import build_brand_profile_block
    from services.festival_service import get_upcoming_festivals
    from services.llm_service import LLMService

    profile = get_user_brand_profile(user_id)
    if not profile:
        return None, {}

    with _feed_locks_guard:
        lock = _feed_locks.setdefault(user_id, threading.Lock())
    with lock:  # a second tab waits for the first one's feed instead of paying for another
        if not force:
            cached = cached_idea_feed(user_id)
            if cached:
                return cached, {}

        category, label, region = profile_market(profile)
        trends = get_trends(category, label, region)
        topics = trends["topics"]
        post_types = PLAYBOOK.get(category, []) + _COMMON_POST_TYPES

        # festival_service labels regions "USA"/"India"; it has no UAE/GCC calendar yet
        festival_regions = {"US": "USA", "India": "India"}
        markets = {festival_regions[r] for r in profile.get("compliance_regions") or profile.get("regions_detected") or []
                   if r in festival_regions}
        occasions = [f for f in get_upcoming_festivals(days_ahead=30) if f["region"] in markets][:3]

        previous = (cached_feed_titles(user_id) if force else [])
        recent = [r.get("story", "")[:140] for r in get_history(limit=10, user_id=user_id) if r.get("story")]
        user_prompt = (
            f"{build_brand_profile_block(user_id)}\n\n"
            f"INDUSTRY: {label}\nMARKET: {REGIONS.get(region, REGIONS[DEFAULT_REGION])[0]}\n\n"
            "TRENDING TOPICS THIS WEEK:\n"
            + ("\n".join(
                f"{i}. {t['topic']}" + (f" - {t['summary']}" if t["summary"] else "")
                + (f" (why now: {t['why_now']})" if t["why_now"] else "")
                + (f" [source: {t['source_title']}]" if t["source_title"] else "")
                for i, t in enumerate(topics)) or "none")
            + "\n\nPOST TYPES:\n" + "\n".join(f"- {p}" for p in post_types)
            + "\n\nUPCOMING OCCASIONS:\n"
            + ("\n".join(f"- {f['name']} ({f['date']}, {f['region']})" for f in occasions) or "none")
            + f"\n\nALREADY SHOWN (do not repeat): {'; '.join(previous) or 'none'}"
            + f"\nRECENTLY POSTED (avoid these topics): {' | '.join(recent) or 'none'}"
        )
        result, usage = LLMService().generate_json(
            IDEA_FEED_SYSTEM_PROMPT.format(trending_count=4, playbook_count=4, dates_count=3),
            user_prompt, temperature=0.8, max_tokens=4000, return_usage=True,
        )

        trending, used = [], set()
        for idea in (result or {}).get("trending") or []:
            try:
                index = int(idea.get("trend_index"))
            except (TypeError, ValueError, AttributeError):
                continue
            if not 0 <= index < len(topics) or index in used:
                continue
            topic = topics[index]
            clean = _clean_idea(idea, {"topic": topic["topic"], "source_title": topic["source_title"],
                                       "source_url": topic["source_url"]})
            if clean:
                used.add(index)
                trending.append(clean)
        playbook = [c for c in (_clean_idea(i, {"post_type": str(i.get("post_type") or "").strip()[:60]})
                                for i in (result or {}).get("playbook") or [] if isinstance(i, dict)) if c]
        dates = [c for c in (_clean_idea(i, {"occasion": str(i.get("occasion") or "").strip()[:80]})
                             for i in (result or {}).get("dates") or [] if isinstance(i, dict)) if c]

        feed = {
            "industry": label,
            "industry_category": category,
            "region": region,
            "trend_key": trend_key(category, region),
            "trend_source": trends["source"],
            "trends_updated_at": trends["fetched_at"].isoformat() if trends["fetched_at"] else None,
            "groups": {"trending": trending[:4], "playbook": playbook[:4], "dates": dates[:3] if occasions else []},
        }
        if trending or playbook or dates:
            save_user_idea_feed(user_id, feed)
        return feed, usage or {}


def cached_feed_titles(user_id: int) -> list[str]:
    from db import get_user_idea_feed

    saved = get_user_idea_feed(user_id)
    groups = ((saved or {}).get("feed") or {}).get("groups") or {}
    return [i.get("title", "") for ideas in groups.values() for i in ideas if i.get("title")]
