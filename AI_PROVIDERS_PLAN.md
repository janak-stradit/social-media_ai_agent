# AI providers and models from Admin: research and plan

How to let an admin switch the AI provider and choose the model for text,
vision, images and video, from Admin, without a redeploy. October 2026.

Decisions taken: API keys are **editable in Admin** (stored encrypted), and
switching is **per capability, with per-feature overrides**.

---

## 1. How it works today

| Capability | Who decides | Where |
|---|---|---|
| Text (captions, hashtags, analysis...) | Which keys are set. With `HEYROUTE_API_KEY`, HeyRoute is the *only* provider; without it, a fixed chain OpenRouter -> Bedrock -> OpenAI -> Gemini | `services/llm_service.py` `__init__` |
| Text model | `HEYROUTE_LLM_MODEL`, `AGENTSCOPE_MODEL`, `BEDROCK_TEXT_MODEL`, Gemini hard-coded `gemini-3.1-flash-lite` | `config.py`, `llm_service.py` |
| Vision (image analysis) | `VISION_PROVIDER` (heyroute / bedrock / local) + `HEYROUTE_VISION_MODEL` | `config.py`, `agents/vision_agent.py` |
| Images | HeyRoute only (`HEYROUTE_IMAGE_API_KEY`). Other providers exist in code but only through `generate_image(ai_model=...)` | `services/media_service.py` `_generate_image_primary` |
| Image model | **Already in Admin**: Image Settings keeps a model list with prices, a default, and a per-user model | `db.get_image_settings`, `users.image_model` |
| Video | HeyRoute only: `HEYROUTE_VIDEO_MODEL` + optional fallback | `media_service.generate_video` |
| Costs | Text: hard-coded per-1K-token rates matched on model names (`_calculate_cost`). Image: Image Settings price. Video: `HEYROUTE_VIDEO_COST_PER_SECOND_USD` | `llm_service.py`, `config.py` |

Problems this causes:

1. **Switching needs a deploy.** Every change is an `ENV_FILE` edit and a
   redeploy, and a wrong value only shows up when users hit errors.
2. **The rules are scattered** over 4 files, with a different mechanism per
   capability.
3. **One model for every task.** Hashtags, trend summaries and website
   analysis all use the same (expensive) model as captions.
4. **Costs are guessed.** Token prices are matched on substrings of model
   names; a new model falls back to a default rate, so credit charges can be
   wrong.
5. **Admin -> AI status is inaccurate.** `/api/models-info` reports Bedrock or
   OpenAI from the keys it sees, even when HeyRoute is what actually runs.

There are 18 text-model call sites (agents and services) and 3 media entry
points, all creating `LLMService()` or `MediaService()` directly.

---

## 2. Concepts

- **Connection**: one account at one provider: a type, a name, a base URL and
  an API key. "HeyRoute - text", "HeyRoute - image" (HeyRoute keys are tied to
  a model group, so it needs one connection per group), "OpenAI", "AWS
  Bedrock".
- **Model**: a model offered by a connection, with the capabilities it can do
  (text, vision, image, video) and its prices.
- **Route**: which model serves a capability or a feature, plus fallbacks
  tried in order and a few settings (reasoning effort, timeout, video length).

Who wins when several apply:

```
user's own image model (existing Image access)  >  feature route  >  capability default  >  ENV_FILE (today's behaviour)
```

---

## 3. Design

### 3.1 Data

Three new tables (a JSON blob in `app_settings` is too loose for keys, prices
and audit):

| Table | Columns |
|---|---|
| `ai_connections` | id, name, kind (`openai_compatible`, `bedrock`, `kie`), base_url, api_key_encrypted, key_last4, extra (JSON: AWS region...), enabled, last_test_at, last_test_ok, last_test_error, created_at, updated_at |
| `ai_models` | id, connection_id, model_id (e.g. `gpt-5.6-terra`), label, capabilities (JSON), price_in_per_1k, price_out_per_1k, price_per_image, price_per_video_second, enabled |
| `ai_routes` | key (`text`, `text.hashtags`, `image`, `video`...), model_id, fallback_model_ids (JSON, ordered), params (JSON), updated_by, updated_at |

Every change writes the audit log (`log_audit("ai.route_changed", ...)`) with
before/after, never the key.

### 3.2 API keys in Admin (encrypted)

- Encrypted with Fernet using a **dedicated `AI_KEYS_ENCRYPTION_KEY`** from
  `ENV_FILE`, not `SECRET_KEY`. We just saw that changing `SECRET_KEY` locked
  out two-step sign-in; provider keys must not break the same way.
- Without `AI_KEYS_ENCRYPTION_KEY`, Admin can't save keys and says why; the
  `ENV_FILE` keys keep working.
- Keys are **write-only**: the page shows "...a1b2" and Replace / Remove,
  never the key. Responses, logs and errors never contain it.
- Changing a key needs an admin with two-step sign-in (`ADMIN_2FA_REQUIRED`
  is already on by default), and is audited.
- `scripts/rotate_ai_keys.py` re-encrypts every key when the encryption key is
  rotated.

### 3.3 Provider adapters

Most providers speak the OpenAI API, so one adapter covers them:

| Kind | Providers | Text | Vision | Image | Video |
|---|---|---|---|---|---|
| `openai_compatible` | HeyRoute, OpenAI, OpenRouter, Gemini (OpenAI endpoint), Z.AI, any custom gateway | yes | yes | yes | HeyRoute and OpenRouter only |
| `bedrock` | AWS Bedrock (Nova) | yes | yes | yes (Nova Canvas) | yes (Nova Reel) |
| `kie` | kie.ai | - | - | yes | - |

The existing per-provider code in `media_service.py` (`_generate_image_heyroute`,
`_generate_image_bedrock`, `_generate_video_heyroute`...) becomes the adapters'
inside, so request details proven in production are kept, not rewritten.

### 3.4 Features that can have their own text model

| Feature key | What it does | Code |
|---|---|---|
| `text.captions` | Writes and refines captions | `agents/caption_agent.py` |
| `text.hashtags` | Hashtags | `agents/hashtag_agent.py` |
| `text.brief` | Reads the brief (themes, emotions) | `agents/story_agent.py` |
| `text.strategy` | Strategy and opportunities | `agents/strategy_agent.py`, `agents/opportunity_agent.py` |
| `text.website` | Website analysis and brand profile | `agents/website_analysis_agent.py`, `services/brand_profile_service.py` |
| `text.trends` | Trends and news ideas | `services/trend_service.py`, `services/news_aggregator.py` |
| `text.compliance` | Compliance checks | `services/compliance_service.py` |
| `text.repurpose` | Link / PDF to post ideas | `services/repurpose_service.py` |
| `text.carousel` | Carousel slide plans | `services/carousel_service.py` |
| `text.hiring` | Hiring posts | `services/hiring_service.py` |
| `text.outreach` | Lead previews | `services/lead_preview_service.py` |
| `text.image_prompt` | Writes image and video prompts | `services/media_service.py` |
| `vision` | Image analysis | `agents/vision_agent.py` |
| `image`, `image.carousel`, `image.edit` | Images | `services/media_service.py` |
| `video` | Video | `services/media_service.py` |

A feature with no route of its own uses its capability's default.

### 3.5 Reading the routes (fast, and the same on every server process)

- `services/ai_router.py`: `resolve(capability, feature) -> [primary, *fallbacks]`,
  each with its connection (decrypted key), model and params.
- Kept in memory per process; every call checks one number,
  `app_settings["ai_routes_version"]`, which Save increments. All gunicorn
  workers and the scheduler pick up a change on their next call, within
  seconds, with one tiny query instead of loading everything.
- If the tables are empty or a route is broken, it falls back to today's
  `ENV_FILE` behaviour, so a bad setting can't stop the app.
- Emergency switch: `AI_ROUTING_FROM_ENV=true` in `ENV_FILE` ignores Admin
  completely.

### 3.6 Call-site changes

- `LLMService(feature="text.hashtags")`: builds its provider list from the
  router instead of the `if HEYROUTE_API_KEY ...` branches. The 18 call sites
  pass a feature name (one line each).
- `MediaService` image, edit, carousel and video: ask the router; the per-user
  image model stays as the top override.
- `vision_agent`: asks the router.
- Costs: `_calculate_cost` and the image and video prices come from
  `ai_models` prices. The hard-coded rates stay only as the last fallback.
- `/api/models-info` (Admin -> AI status) reports the real routes.
- Image Settings: the model list and prices move into AI providers. The daily
  image limit stays in Image Settings.

### 3.7 Admin -> AI providers (new tab)

1. **Connections**: add (type, name, base URL, key), **Test** (lists models,
   or sends one tiny request: shows OK, response time or the provider's
   error), enable/disable, replace key.
2. **Models**: **Fetch models** from the connection's `/models` endpoint
   (OpenAI-compatible providers and Bedrock can list them) or add one by hand;
   tick capabilities; enter prices.
3. **Routing**: one row per capability (Text, Vision, Image, Video), each with
   a model, fallbacks in order and its settings. Text expands to the features
   in 3.4, each "Same as Text" or its own model. **Test this route** runs a
   real call and shows the answer, time and cost.
4. **History**: who changed what and when, with **Revert**.

Save is refused when a chosen model's connection is disabled or its last test
failed, so a broken setup can't go live by accident.

---

## 4. Phases

| Phase | What ships | Size |
|---|---|---|
| 1. Foundation (done) | Tables, encryption with `AI_KEYS_ENCRYPTION_KEY`, router with `ENV_FILE` fallback, seeding the tables from today's settings on first start (nothing changes for users), Admin: Connections + Models (fetch, test, prices), honest AI status | 3-4 days |
| 2. Text | Text routing + per-feature overrides, `LLMService(feature=...)` at the 18 call sites, model-based token costs, Test this route | 3 days |
| 3. Images and vision | Image, edit, carousel and vision routes; Image Settings models moved in; per-user image model kept | 2-3 days |
| 4. Video and polish | Video route with fallbacks and per-model settings, History + Revert, key rotation script, Help article | 2-3 days |

Each phase is releasable on its own: until a route is saved in Admin, that
capability keeps running exactly as today.

---

## 5. Risks and open questions

1. **HeyRoute's `/models` endpoint**: OpenAI-compatible gateways usually
   offer it, but it isn't confirmed for HeyRoute. If it isn't there, models
   are added by hand. Check in phase 1.
2. **Provider differences**: `reasoning_effort` only works on some models,
   video models each take different durations, sizes and reference images
   (`_heyroute_video_body`), and some image models can't edit. Each model's
   capabilities and settings must be explicit, not assumed.
3. **Prices are entered by hand** and charged to users' credits; a wrong
   price over- or under-charges. Show the price next to every model choice,
   and audit price changes.
4. **Keys in the database**: a database copy plus the encryption key exposes
   them. Keep `AI_KEYS_ENCRYPTION_KEY` only in `ENV_FILE`, never in the
   database or logs.
5. **Test calls cost money**: limit them per admin per minute.
6. **Fallbacks hide outages**: when a fallback serves a request, log it and
   show it on the AI status page, so a failing primary is noticed.

## 6. Testing

- Router: precedence (user > feature > capability > env), fallback order,
  disabled connections skipped, version change picked up, env switch.
- Encryption: round trip, write-only API (a key never appears in any
  response), missing encryption key refused.
- Every call site passes a feature, checked by a test that scans for
  `LLMService()` without one.
- Costs from model prices, for text, image and video.
- Admin endpoints: admin-only, two-step sign-in required for key changes,
  audited.
