"""Which AI provider and model serve each capability (AI_PROVIDERS_PLAN.md).

Admin -> AI providers keeps three things in the database (db.AiConnection,
db.AiModel, db.AiRoute):

  connection  one account at one provider (its key encrypted, services/ai_keys.py,
              or read from an ENV_FILE variable)
  model       a model a connection offers, what it can do, and its prices
  route       which model serves "text", "vision", "image" or "video" - or one
              feature such as "text.hashtags" - plus fallbacks in order

Until a route is saved for a capability, it runs on the ENV_FILE settings
exactly as before (current() describes what that is). AI_ROUTING_FROM_ENV=true
ignores Admin entirely.

Phase 1 (this file): the catalog, seeding it from today's settings, testing
connections, fetching their models, and an honest description of what runs.
Phases 2-4 make the text, image, vision and video code ask resolve().
"""

import logging
import os
import threading

import requests

from config import Config

logger = logging.getLogger(__name__)

CAPABILITIES = ("text", "vision", "image", "video")
CAPABILITY_NAMES = {"text": "Text", "vision": "Image analysis", "image": "Images", "video": "Video"}

# Features that can have a text model of their own (phase 2); no route = "text"
FEATURES = {
    "text.captions": "Captions",
    "text.hashtags": "Hashtags",
    "text.brief": "Reading the brief",
    "text.strategy": "Strategy and opportunities",
    "text.website": "Website analysis and brand profile",
    "text.trends": "Trends and news ideas",
    "text.compliance": "Compliance checks",
    "text.repurpose": "Ideas from a link or PDF",
    "text.carousel": "Carousel slide plans",
    "text.hiring": "Hiring posts",
    "text.outreach": "Lead previews",
    "text.image_prompt": "Image and video prompts",
}

KINDS = {
    "openai_compatible": "OpenAI-compatible (HeyRoute, OpenAI, OpenRouter, Gemini, Z.AI, a custom gateway)",
    "bedrock": "AWS Bedrock",
    "kie": "kie.ai (images)",
}
TEST_TIMEOUT = 15  # seconds


class ConnectionProblem(Exception):
    """A connection can't be used or reached; the message is for the admin."""


# ── Keys ─────────────────────────────────────────────────────────────────


def connection_key(connection: dict) -> str | None:
    """The API key a connection uses: the one typed in Admin (decrypted), else
    its ENV_FILE variable. `connection` must come from
    db.get_ai_connection(..., with_secret=True)."""
    from services import ai_keys

    if connection.get("api_key_encrypted"):
        return ai_keys.decrypt(connection["api_key_encrypted"])
    if connection.get("key_env"):
        return os.getenv(connection["key_env"]) or None
    return None


# ── What runs today (ENV_FILE) ───────────────────────────────────────────


def _openrouter_key_env() -> str | None:
    if os.getenv("OPENROUTER_API_KEY"):
        return "OPENROUTER_API_KEY"
    key = Config.OPENAI_API_KEY or ""
    return "OPENAI_API_KEY" if key.startswith("sk-or-") or "openrouter" in key.lower() else None


def _has_aws() -> bool:
    return bool(getattr(Config, "AWS_PROFILE", None) or (
        getattr(Config, "AWS_ACCESS_KEY_ID", None) and getattr(Config, "AWS_SECRET_ACCESS_KEY", None)))


def _image_default_model() -> str:
    try:
        from db import get_image_settings

        return get_image_settings()["default_model"]
    except Exception:
        return Config.HEYROUTE_IMAGE_MODEL


def env_current() -> dict:
    """What each capability runs on from the ENV_FILE settings, mirroring the
    rules in services/llm_service.py, services/hf_service.py and
    services/media_service.py. -> {capability: {provider, model, fallbacks, note}}"""
    if Config.USE_MOCK_LLM:
        text = {"provider": "Mock (USE_MOCK_LLM)", "model": "mock-llm-v1", "fallbacks": []}
    elif Config.HEYROUTE_API_KEY:
        text = {"provider": "HeyRoute", "model": Config.HEYROUTE_LLM_MODEL, "fallbacks": []}
    else:
        chain = []
        if _openrouter_key_env():
            chain.append(("OpenRouter", Config.AGENTSCOPE_MODEL))
        if _has_aws():
            chain.append(("AWS Bedrock", Config.BEDROCK_TEXT_MODEL))
        if Config.OPENAI_API_KEY and not _openrouter_key_env():
            chain.append(("OpenAI", Config.AGENTSCOPE_MODEL))
        if getattr(Config, "GOOGLE_API_KEY", None) or os.getenv("GOOGLE_API_KEY"):
            chain.append(("Google Gemini", "gemini-3.1-flash-lite"))
        if not chain:
            chain.append(("Mock (no text provider configured)", "mock-llm-v1"))
        text = {"provider": chain[0][0], "model": chain[0][1], "fallbacks": [f"{p}: {m}" for p, m in chain[1:]]}

    vision_kind = Config.VISION_PROVIDER
    if vision_kind == "heyroute":
        vision = {"provider": "HeyRoute", "model": Config.HEYROUTE_VISION_MODEL, "fallbacks": []}
    elif vision_kind == "bedrock":
        vision = {"provider": "AWS Bedrock", "model": Config.BEDROCK_VISION_MODEL, "fallbacks": []}
    else:
        vision = {"provider": "Local captioning model", "model": "BLIP (on the server)", "fallbacks": [],
                  "note": "Downloads a large model; not meant for the production server."}

    if Config.HEYROUTE_IMAGE_API_KEY:
        image = {"provider": "HeyRoute", "model": _image_default_model(), "fallbacks": [],
                 "note": "Users with their own image model (Image access) use that one."}
    else:
        image = {"provider": "Not configured", "model": None, "fallbacks": [],
                 "note": "Images need HEYROUTE_IMAGE_API_KEY."}

    if Config.HEYROUTE_VIDEO_API_KEY:
        video = {"provider": "HeyRoute", "model": Config.HEYROUTE_VIDEO_MODEL,
                 "fallbacks": [m for m in [Config.HEYROUTE_VIDEO_FALLBACK_MODEL] if m]}
    else:
        video = {"provider": "Not configured", "model": None, "fallbacks": [], "note": "Video needs HEYROUTE_VIDEO_API_KEY."}
    return {"text": text, "vision": vision, "image": image, "video": video}


def current() -> dict:
    """What each capability runs on right now, and whether that comes from
    Admin or from ENV_FILE. In phase 1 every capability still runs on
    ENV_FILE; routes saved in Admin take over as phases 2-4 land."""
    out = {}
    for capability, info in env_current().items():
        out[capability] = {**info, "source": "env", "name": CAPABILITY_NAMES[capability]}
    return out


# ── Seeding: today's settings become the starting catalog ────────────────


def _seed_plan() -> list[dict]:
    """Connections (with their models) for every provider ENV_FILE has a key for.
    Keys stay in ENV_FILE: a seeded connection names the variable, it doesn't copy it."""
    plan = []
    image_prices = {}
    try:
        from db import get_image_settings

        for m in get_image_settings()["models"]:
            image_prices[m["id"]] = m.get("price")
    except Exception:
        image_prices[Config.HEYROUTE_IMAGE_MODEL] = Config.HEYROUTE_IMAGE_COST_USD

    if Config.HEYROUTE_API_KEY:
        models = [{"model_id": Config.HEYROUTE_LLM_MODEL, "capabilities": ["text"]}]
        if Config.HEYROUTE_VISION_MODEL == Config.HEYROUTE_LLM_MODEL:
            models[0]["capabilities"].append("vision")
        else:
            models.append({"model_id": Config.HEYROUTE_VISION_MODEL, "capabilities": ["vision"]})
        plan.append({"name": "HeyRoute - text", "kind": "openai_compatible", "base_url": Config.HEYROUTE_BASE_URL,
                     "key_env": "HEYROUTE_API_KEY", "models": models})
    if Config.HEYROUTE_IMAGE_API_KEY:
        plan.append({"name": "HeyRoute - images", "kind": "openai_compatible", "base_url": Config.HEYROUTE_BASE_URL,
                     "key_env": "HEYROUTE_IMAGE_API_KEY",
                     "models": [{"model_id": mid, "capabilities": ["image"], "price_per_image": price}
                                for mid, price in image_prices.items()]})
    if Config.HEYROUTE_VIDEO_API_KEY:
        plan.append({"name": "HeyRoute - video", "kind": "openai_compatible", "base_url": Config.HEYROUTE_BASE_URL,
                     "key_env": "HEYROUTE_VIDEO_API_KEY",
                     "models": [{"model_id": mid, "capabilities": ["video"],
                                 "price_per_video_second": Config.HEYROUTE_VIDEO_COST_PER_SECOND_USD}
                                for mid in [Config.HEYROUTE_VIDEO_MODEL, Config.HEYROUTE_VIDEO_FALLBACK_MODEL] if mid]})
    openrouter_env = _openrouter_key_env()
    if openrouter_env:
        plan.append({"name": "OpenRouter", "kind": "openai_compatible", "base_url": "https://openrouter.ai/api/v1",
                     "key_env": openrouter_env, "models": [{"model_id": Config.AGENTSCOPE_MODEL, "capabilities": ["text"]}]})
    if Config.OPENAI_API_KEY and not openrouter_env:
        plan.append({"name": "OpenAI", "kind": "openai_compatible", "base_url": "https://api.openai.com/v1",
                     "key_env": "OPENAI_API_KEY", "models": [{"model_id": Config.AGENTSCOPE_MODEL, "capabilities": ["text"]}]})
    if getattr(Config, "GOOGLE_API_KEY", None) or os.getenv("GOOGLE_API_KEY"):
        plan.append({"name": "Google Gemini", "kind": "openai_compatible",
                     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                     "key_env": "GOOGLE_API_KEY", "models": [{"model_id": "gemini-3.1-flash-lite", "capabilities": ["text"]}]})
    if Config.Z_AI_API_KEY:
        plan.append({"name": "Z.AI", "kind": "openai_compatible", "base_url": Config.Z_AI_BASE_URL.rstrip("/"),
                     "key_env": "Z_AI_API_KEY",
                     "models": [{"model_id": Config.Z_AI_IMAGE_MODEL, "capabilities": ["image"]},
                                {"model_id": Config.Z_AI_VIDEO_MODEL, "capabilities": ["video"]}]})
    if Config.KIE_API_KEY:
        plan.append({"name": "kie.ai", "kind": "kie", "base_url": None, "key_env": "KIE_API_KEY", "models": []})
    if _has_aws():
        models: dict[str, list] = {}
        for capability, mid in (("text", Config.BEDROCK_TEXT_MODEL), ("vision", Config.BEDROCK_VISION_MODEL),
                                ("image", Config.BEDROCK_IMAGE_MODEL), ("video", Config.BEDROCK_VIDEO_MODEL)):
            models.setdefault(mid, []).append(capability)
        plan.append({"name": "AWS Bedrock", "kind": "bedrock", "base_url": None,
                     "key_env": "AWS_PROFILE" if getattr(Config, "AWS_PROFILE", None) else "AWS_ACCESS_KEY_ID",
                     "extra": {"region": Config.AWS_REGION},
                     "models": [{"model_id": mid, "capabilities": caps} for mid, caps in models.items()]})
    return plan


_seed_lock = threading.Lock()


def seed_from_env() -> bool:
    """Fill the catalog from ENV_FILE once (the first time Admin opens it).
    Changes nothing that runs. -> True when it seeded now."""
    from db import (
        get_setting,
        list_ai_connections,
        save_ai_connection,
        save_ai_model,
        save_setting,
    )

    with _seed_lock:
        if get_setting("ai_seeded", "") or list_ai_connections():
            return False
        for item in _seed_plan():
            conn = save_ai_connection(None, name=item["name"], kind=item["kind"], base_url=item["base_url"],
                                      key_env=item["key_env"], extra=item.get("extra") or {}, enabled=True)
            for model in item["models"]:
                try:
                    save_ai_model(None, connection_id=conn["id"], **model)
                except ValueError:
                    pass  # the same model twice
        save_setting("ai_seeded", "1")
        return True


# ── Testing a connection and listing its models ──────────────────────────


def check_base_url(url: str | None) -> str:
    """A provider address must be a public https URL (the server calls it)."""
    from services import website_scraper_service as web

    url = (url or "").strip().rstrip("/")
    if not url.startswith("https://"):
        raise ConnectionProblem("The address must start with https://")
    if web._check_url(url) is not None:  # pylint: disable=protected-access
        raise ConnectionProblem("That address can't be reached from here, or points to a private network.")
    return url


def _openai_models(connection: dict) -> list[str]:
    key = connection_key(connection)
    if not key:
        raise ConnectionProblem("This connection has no API key.")
    base = check_base_url(connection.get("base_url"))
    try:
        resp = requests.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=TEST_TIMEOUT)
    except requests.exceptions.Timeout as err:
        raise ConnectionProblem("The provider didn't answer within 15 seconds.") from err
    except requests.exceptions.RequestException as err:
        raise ConnectionProblem("Couldn't reach the provider. Check the address.") from err
    if resp.status_code in (401, 403):
        raise ConnectionProblem("The provider refused the API key.")
    if resp.status_code == 404:
        raise ConnectionProblem("Connected, but this provider can't list its models here. Add them by hand.")
    if not resp.ok:
        raise ConnectionProblem(f"The provider answered with an error ({resp.status_code}).")
    try:
        data = resp.json()
    except ValueError as err:
        raise ConnectionProblem("The provider's answer wasn't readable.") from err
    items = data.get("data") if isinstance(data, dict) else data
    ids = sorted({str(m.get("id")) for m in items or [] if isinstance(m, dict) and m.get("id")})
    return ids


def _bedrock_models(connection: dict) -> list[str]:
    region = (connection.get("extra") or {}).get("region") or Config.AWS_REGION
    try:
        import boto3

        session = boto3.Session(profile_name=Config.AWS_PROFILE) if Config.AWS_PROFILE else boto3.Session(
            aws_access_key_id=Config.AWS_ACCESS_KEY_ID, aws_secret_access_key=Config.AWS_SECRET_ACCESS_KEY)
        listed = session.client("bedrock", region_name=region).list_foundation_models()
    except Exception as err:
        raise ConnectionProblem(f"AWS didn't list the models: {str(err)[:160]}") from err
    return sorted({m["modelId"] for m in listed.get("modelSummaries", []) if m.get("modelId")})


def remote_models(connection: dict) -> list[str]:
    """The model ids the provider offers. Raises ConnectionProblem."""
    if connection["kind"] == "openai_compatible":
        return _openai_models(connection)
    if connection["kind"] == "bedrock":
        return _bedrock_models(connection)
    raise ConnectionProblem("kie.ai can't list its models. Add them by hand.")


def test_connection(connection_id: int) -> dict:
    """Reach the provider with the connection's key (listing models costs
    nothing). Records the result. -> {ok, message, models}"""
    from db import get_ai_connection, record_ai_connection_test

    connection = get_ai_connection(connection_id, with_secret=True)
    if connection is None:
        raise ConnectionProblem("Connection not found.")
    if connection["kind"] == "kie":
        ok = bool(connection_key(connection))
        message = "Key is set. kie.ai can't be tested from here - it is checked when an image is made." if ok \
            else "This connection has no API key."
        record_ai_connection_test(connection_id, ok, None if ok else message)
        return {"ok": ok, "message": message, "models": []}
    try:
        models = remote_models(connection)
    except ConnectionProblem as err:
        message = str(err)
        reachable = message.startswith("Connected,")
        record_ai_connection_test(connection_id, reachable, None if reachable else message)
        return {"ok": reachable, "message": message, "models": []}
    record_ai_connection_test(connection_id, True, None)
    return {"ok": True, "message": f"Connected. The provider offers {len(models)} model{'s' if len(models) != 1 else ''}.",
            "models": models}


# ── Resolving a route (used by the text, image, vision and video code from phase 2) ──

_cache = {"version": None, "routes": {}, "models": {}, "connections": {}}
_cache_lock = threading.Lock()


def _load(version: str) -> None:
    from db import get_ai_connection, list_ai_models, list_ai_routes

    models = {m["id"]: m for m in list_ai_models()}
    connections = {}
    for m in models.values():
        if m["connection_id"] not in connections:
            connections[m["connection_id"]] = get_ai_connection(m["connection_id"], with_secret=True)
    _cache.update(version=version, routes={r["key"]: r for r in list_ai_routes()}, models=models,
                  connections=connections)


def _fresh() -> dict:
    """The routes, re-read only when Admin saved something (ai_routes_version):
    one small query per call keeps every server process in step."""
    from db import get_setting

    version = get_setting("ai_routes_version", "0")
    with _cache_lock:
        if _cache["version"] != version:
            _load(version)
        return _cache


def _choice(cache: dict, model_pk: int | None) -> dict | None:
    model = cache["models"].get(model_pk)
    connection = cache["connections"].get(model["connection_id"]) if model else None
    if not model or not connection or not model["enabled"] or not connection["enabled"]:
        return None
    key = connection_key(connection)
    if not key and connection["kind"] != "bedrock":
        return None
    return {"model": model["model_id"], "model_pk": model["id"], "connection_id": connection["id"],
            "connection": connection["name"], "kind": connection["kind"], "base_url": connection["base_url"],
            "api_key": key, "extra": connection.get("extra") or {}, "prices": {
                k: model[k] for k in ("price_in_per_1k", "price_out_per_1k", "price_per_image", "price_per_video_second")}}


def resolve(capability: str, feature: str | None = None) -> list[dict] | None:
    """The models to try, in order, for a capability or one of its features
    (the feature's route, else the capability's). None = no usable route:
    run on ENV_FILE as before. Never raises - a broken setting falls back."""
    if Config.AI_ROUTING_FROM_ENV:
        return None
    try:
        cache = _fresh()
    except Exception as err:
        logger.warning(f"AI routes unavailable, using ENV_FILE settings: {err}")
        return None
    for key in ([feature] if feature else []) + [capability]:
        route = cache["routes"].get(key)
        if not route:
            continue
        choices = [c for c in (_choice(cache, pk) for pk in [route["model_id"], *route["fallback_model_ids"]]) if c]
        if choices:
            for c in choices:
                c["params"] = route["params"]
            return choices
    return None


def connection_for_model(model_id: str | None, capability: str) -> dict | None:
    """The connection that offers `model_id` for `capability` in Admin -> AI
    providers (an enabled model on an enabled, OpenAI-compatible connection
    with a key) - so a model chosen elsewhere (Image Settings, a user's own
    image model) is sent to the provider that has it. One that passed its
    last test is preferred. None = not in the catalog: the ENV_FILE provider."""
    if not model_id or Config.AI_ROUTING_FROM_ENV:
        return None
    try:
        cache = _fresh()
    except Exception as err:  # noqa: BLE001 - no database: ENV_FILE
        logger.warning(f"AI catalog unavailable, using ENV_FILE settings: {err}")
        return None
    found = []
    for model in cache["models"].values():
        if model["model_id"] != model_id or capability not in model["capabilities"]:
            continue
        choice = _choice(cache, model["id"])
        if choice and choice["kind"] == "openai_compatible" and choice["base_url"]:
            connection = cache["connections"][model["connection_id"]]
            found.append((connection.get("last_test_ok") is not True, connection["id"], choice))
    return sorted(found, key=lambda f: f[:2])[0][2] if found else None
