"""Generating a post: the pipeline behind Studio Chat's Generate, callable
without a web request - so Autopilot (AUTOPILOT_PLAN.md) runs exactly the
same steps from the background scheduler.

    generate_post(user_id, data, progress_id=None) -> the same result the
    /api/generate endpoint returns (captions, hashtags, quality and
    compliance per platform, run_id, conversation_id, usage)

`data` holds the same fields the endpoint takes (story, platforms, tone,
brand_voice, language, selected_outputs, image_path, precomputed_analysis,
include_strategy, previous_context, target_company, conversation_id,
keep_media_from_run_id, version_of_run_id, progress_id). A post the user's
plan or credit doesn't allow raises PostBlocked with the endpoint's response.

The agents and helpers are shared with api/routes.py and looked up there at
call time (`r.caption_agent`, ...), so the endpoint and this module always
use the same ones - and tests that replace them on api.routes cover both.
"""

import json
import logging
import os
import time
import typing
import uuid

from config import Config
from services import hashtag_service

logger = logging.getLogger(__name__)


class PostBlocked(Exception):
    """The user's plan or credit doesn't allow a new post. `payload` and
    `status` are the endpoint's JSON response and HTTP status."""

    def __init__(self, payload: dict, status: int):
        super().__init__(payload.get("error") or "This post can't be made right now.")
        self.payload = payload
        self.status = status


def _conversation_for(user_id, data: dict, title: str) -> int | None:
    """The conversation to save this message into: the given one, or a new
    one. None when it can't be saved - the run is then saved alone."""
    try:
        from db import ensure_conversation

        return ensure_conversation(user_id, data.get("conversation_id"), title)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[DB] Could not get conversation: {e}")
        return None


def generate_post(user_id, data: dict) -> dict:
    """Runs the whole pipeline for one brief (see the module docstring)."""
    from api import routes as r

    started = time.time()

    story = data.get("story", "")
    image_path = data.get("image_path")
    # When the caller already ran /api/analyze-story (the research-first Studio
    # Chat flow: research a brief, show it, then let the user pick platform/
    # output before generating) it can pass that exact analysis back here so
    # generation reuses it verbatim, instead of the Story Agent silently
    # re-analyzing the same brief a second time and potentially landing on a
    # different set of research notes than what the user already saw.
    precomputed_analysis = data.get("precomputed_analysis")
    platforms = data.get("platforms", ["facebook", "instagram", "linkedin"])
    tone = data.get("tone")
    brand_voice = data.get("brand_voice", "Standard Enterprise")
    from services.language_service import normalize as _normalize_language

    language = _normalize_language(data.get("language"))  # Style -> Language (services/language_service.py)
    # Off by default: the strategy output (posting schedule/forecast) isn't
    # shown anywhere, and it was the slowest LLM call of the whole pipeline.
    include_strategy = data.get("include_strategy", False)
    previous_context = data.get("previous_context")
    target_company = data.get("target_company")
    # The text pipeline (Caption/Hashtag/Strategy/Reviewer/Brand Guardrail)
    # always runs, regardless of which output checkboxes are selected - an
    # image/video post still needs a caption and hashtags to actually publish
    # with. selected_outputs only controls which MEDIA (image/video) gets
    # generated alongside it; it's no longer possible to end up with "No
    # caption generated" just because Text wasn't checked.
    generate_text = True

    # Live step progress for the page (GET /api/generate/progress/<id>)
    from services import progress_store

    progress_id = data.get("progress_id")

    def _progress(step, state):
        progress_store.set_step(user_id, progress_id, step, state)

    # Studio Chat user's own brand context, derived from their onboarding
    # website (see services/brand_profile_service.py) - "" when they don't
    # have one (StradIT's own internal users, Enterprise, scrape failed).
    from services.brand_profile_service import build_brand_profile_block

    brand_profile_block = build_brand_profile_block(user_id)
    brand_company_name = r.COMPANY_NAME
    if brand_profile_block:
        try:
            from db import get_user_brand_profile

            _profile = get_user_brand_profile(user_id)
            if _profile and _profile.get("company_name"):
                brand_company_name = _profile["company_name"]
        except Exception:
            pass

    # Credit Limit Check
    if r.DB_AVAILABLE and user_id:
        # The block is raised after the try: a failing check is only logged,
        # but a reached limit must always stop the post
        credit_block = None
        try:
            stats = r.get_user_usage_stats(user_id)
            if r._credit_blocked(stats):
                limit_val = stats.get("credit_limit", Config.DEFAULT_CREDIT_USD)
                credit_block = {
                    "error": f"Credit limit reached ({r.format_money(limit_val, stats.get('currency', 'USD'))}). Please request a credit extension from admin.",
                    "credit_limit_exceeded": True,
                    "credit_limit": limit_val,
                    "used_credits": stats.get("used_credits", 0.0),
                    "remaining_credits": 0.0,
                    "has_pending_request": stats.get("has_pending_request", False),
                }
        except Exception as _cred_err:
            logger.warning(f"[Credits] Check error: {_cred_err}")
        if credit_block:
            raise PostBlocked(credit_block, 402)
        # A new post counts against the plan's monthly posts (refining one doesn't: /api/refine)
        blocked = r._posts_blocked_payload(user_id)
        if blocked:
            raise PostBlocked(blocked, 403)

    # Multi-Turn Refinement Context Integration
    if previous_context:
        story_prompt = (
            f"Follow-up Refinement Request: {story}\n\n[PREVIOUS TURN CONTEXT & OUTPUTS]:\n{previous_context}"
        )
    else:
        story_prompt = story

    # Inject Scraper Explorer Intelligence
    if target_company and target_company != "None":
        scraper = r.ScraperService()
        intelligence = scraper.get_company_talking_points(target_company)
        if intelligence:
            story_prompt += f"\n\n{intelligence}"

    total_tokens = 0
    total_cost_usd = 0.0
    agents_executed = []

    _progress("story", "active")
    # Step 0: RAG Memory Context Retrieval from ChromaDB
    retrieved_memories = r.memory_service.retrieve_context(story, user_id=user_id, n_results=3)
    mem_prompt = r.memory_service.format_memory_prompt(retrieved_memories)

    # Hashtags that did well on this brand's own posts, offered to the
    # hashtag agent to reuse where they fit (none until posts have results)
    proven_tags = {}
    if generate_text and platforms and user_id:
        try:
            from db import proven_hashtags

            proven_tags = {p: tags for p, tags in proven_hashtags(user_id).items() if p in platforms}
        except Exception as tag_err:  # noqa: BLE001 - hashtags are written without them
            logger.warning(f"Proven hashtags unavailable: {tag_err}")

    # Hashtags only need the post's themes, which the brief already gives:
    # they start now, alongside the research, instead of after it (each AI
    # call can take 10-30s, so waiting for one another is what makes it slow).
    # Not with an attached image (its colours feed the hashtags).
    early_hashtags, early_pool = None, None
    if generate_text and platforms and not (image_path and os.path.exists(image_path)):
        from concurrent.futures import ThreadPoolExecutor

        early_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gen-tags")
        early_hashtags = early_pool.submit(
            r.hashtag_agent.generate_hashtags_batch,
            platforms,
            {"themes": [story[:300]], "emotions": []},
            None,
            memory_context=mem_prompt,
            brand_profile_block=brand_profile_block,
            language=language,
            proven=proven_tags,
        )
        early_pool.shutdown(wait=False)  # the thread finishes the call; nothing else runs on it
        _progress("hashtag", "active")

    # Step 1: Analyze story. The research runs in the background: the
    # caption is written from the brief (caption_input), not from the
    # research, so it starts straight away and appears on the page within
    # seconds. The research is collected below, before the steps that use
    # it (strategy, image/video prompts, the saved run).
    research_future = None
    if "STRATEGY SYNTHESIS:" in story_prompt:
        caption_input = story_prompt
        story_analysis = {"themes": ["Strategy", "Industry"], "emotions": ["Professional"]}
        story_usage = None
    elif isinstance(precomputed_analysis, dict) and precomputed_analysis:
        story_analysis = precomputed_analysis
        caption_input = r.extract_prompt_for_type(story_prompt, "text")
        story_usage = None
    else:
        from concurrent.futures import ThreadPoolExecutor

        research_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gen-research")
        research_future = research_pool.submit(
            r.story_agent.analyze,
            story_prompt,
            memory_context=mem_prompt,
            return_usage=True,
            brand_profile_block=brand_profile_block,
            # Live: the pipeline card shows the research while it's written
            on_partial=(
                (lambda text: _progress("research", json.dumps(r._partial_research(text))))
                if progress_store.valid_id(progress_id)
                else None
            ),
        )
        research_pool.shutdown(wait=False)
        story_analysis, story_usage = None, None
        caption_input = r.extract_prompt_for_type(story_prompt, "text")

    # Step 2: Analyze image if provided
    vision_analysis = None
    if image_path and os.path.exists(image_path):
        _progress("vision", "active")
        vision_analysis = r.vision_agent.analyze_image(image_path)
        _progress("vision", "done")
        agents_executed.append(
            {
                "agent": "VisionAgent",
                "name": "Vision Agent",
                "role": "Analyzed visual asset, color palette & image objects",
                "status": "completed",
            }
        )

    # Step 3+4: Captions and hashtags
    # A brief is only treated as tied to a specific StradIT project/service
    # when it came from a competitor Strategy Synthesis or a target company
    # was explicitly selected - a plain Studio Chat brief with neither is
    # general thought leadership and shouldn't be forced to pitch a product.
    has_project_context = bool("STRATEGY SYNTHESIS:" in story_prompt or (target_company and target_company != "None"))
    # Captions and hashtags don't depend on each other, so every platform's
    # caption and hashtag set is generated at the same time instead of
    # one LLM call after another.
    captions = {}
    hashtags = {}
    if generate_text:
        from concurrent.futures import ThreadPoolExecutor

        def _caption_for(platform):
            def _draft(text, platform=platform):
                # The caption so far, as the page's live preview ("draft:<platform>")
                caption = (r._partial_json(text) or {}).get("primary_caption")
                if isinstance(caption, str) and caption.strip():
                    _progress(f"draft:{platform}", caption[:2000])

            return r.caption_agent.generate_caption(
                platform,
                caption_input,
                vision_analysis,
                tone,
                mem_prompt,
                brand_voice,
                has_project_context,
                brand_profile_block,
                on_partial=_draft if progress_store.valid_id(progress_id) else None,
                language=language,
            )

        def _all_hashtags():
            # Every platform's set in one call (see HashtagAgent.generate_hashtags_batch)
            if early_hashtags is not None:  # started with the research (above)
                try:
                    return early_hashtags.result()
                except Exception as tag_err:  # noqa: BLE001 - try once more with the research
                    logger.warning(f"Early hashtags failed, retrying: {tag_err}")
            try:
                return r.hashtag_agent.generate_hashtags_batch(
                    platforms,
                    research_future.result()[0] if research_future is not None else story_analysis,
                    vision_analysis,
                    memory_context=mem_prompt,
                    brand_profile_block=brand_profile_block,
                    language=language,
                    proven=proven_tags,
                )
            except Exception as tag_err:  # a post without hashtags beats no post at all
                logger.warning(f"Hashtags failed: {tag_err}")
                return {p: {"hashtags": [], "_usage": {}} for p in platforms}

        _progress("caption", "active")
        _progress("hashtag", "active")
        with ThreadPoolExecutor(max_workers=min(8, len(platforms) + 1), thread_name_prefix="gen") as pool:
            caption_futures = {p: pool.submit(_caption_for, p) for p in platforms}
            hashtag_future = pool.submit(_all_hashtags)
            r._mark_done_when_finished(list(caption_futures.values()), lambda: _progress("caption", "done"))

            # Show each caption on the page the moment it's written, while the
            # rest of the pipeline (hashtags, checks) is still running.
            def _preview_caption(platform, future):
                if future.exception() is None:
                    text = (future.result() or {}).get("primary_caption") or ""
                    if text:
                        _progress(f"preview:{platform}", text[:2000])  # same length as the live draft

            for _p, _future in caption_futures.items():
                _future.add_done_callback(lambda f, platform=_p: _preview_caption(platform, f))
            r._mark_done_when_finished([hashtag_future], lambda: _progress("hashtag", "done"))

            for platform, future in caption_futures.items():
                res = future.result()
                usage = res.pop("usage", None) or {}
                total_tokens += usage.get("total_tokens", 0)
                total_cost_usd += usage.get("cost_usd", 0.0)
                captions[platform] = res
            all_tags = hashtag_future.result()
            for platform in platforms:
                res = all_tags.get(platform) or {"hashtags": []}
                usage = res.pop("_usage", None) or {}
                total_tokens += usage.get("total_tokens", 0)
                total_cost_usd += usage.get("cost_usd", 0.0)
                hashtags[platform] = res

        agents_executed.append(
            {
                "agent": "CaptionAgent",
                "name": "Caption Agent",
                "role": f"Wrote the caption for {', '.join(platforms)} ('{brand_voice}' voice)",
                "status": "completed",
            }
        )
        agents_executed.append(
            {
                "agent": "HashtagAgent",
                "name": "Hashtag Agent",
                "role": "Curated broad, niche & brand hashtags for each platform"
                + (", reusing ones that did well on your posts where they fit" if proven_tags else ""),
                "status": "completed",
            }
        )

    # The research (started in the background in step 1) - needed from here on
    if research_future is not None:
        story_analysis, story_usage = research_future.result()

    if story_usage:
        total_tokens += story_usage.get("total_tokens", 0)
        total_cost_usd += story_usage.get("cost_usd", 0.0)
    _progress("story", "done")

    agents_executed.append(
        {
            "agent": "StoryAgent",
            "name": "Story & Memory Agent",
            "role": (
                "Reused the research already shown to you"
                if isinstance(precomputed_analysis, dict) and precomputed_analysis
                else "Analyzed narrative themes, emotional tone & retrieved brand memories"
            ),
            "status": "completed",
        }
    )

    # Step 5: Generate strategy
    strategies = {}
    if include_strategy and generate_text:
        try:
            strategies = r.strategy_agent.generate_all_strategies(
                story_analysis, memory_context=mem_prompt, platforms=platforms
            )
        except Exception as strat_err:  # not shown in Studio Chat - never fail the post over it
            logger.warning(f"[Strategy] Skipped: {strat_err}")
            strategies = {}
        strat_usage = strategies.pop("_usage", {})
        total_tokens += strat_usage.get("total_tokens", 0)
        total_cost_usd += strat_usage.get("cost_usd", 0.0)

        agents_executed.append(
            {
                "agent": "StrategyAgent",
                "name": "Strategy Agent",
                "role": "Calculated optimal posting schedule & engagement forecasts",
                "status": "completed",
            }
        )

    # Step 6: Quality checks (code, no LLM - see agents/r.reviewer_agent.py).
    # A caption that fails a check gets ONE rewrite with the concrete
    # feedback; all rewrites run at the same time.
    quality_evaluations = {}
    compliance_results: dict[str, dict] = {}
    refinements_count = 0

    if generate_text:
        _progress("reviewer", "active")
        for platform in platforms:
            tags = hashtags.get(platform) or {}
            result = r.reviewer_agent.evaluate(
                platform=platform,
                caption=(captions.get(platform) or {}).get("primary_caption", ""),
                hashtags=tags.get("hashtags", []),
            )
            trimmed = result.pop("hashtags")
            if tags:
                tags["hashtags"] = trimmed
            quality_evaluations[platform] = result

        to_fix = [p for p in platforms if quality_evaluations[p]["needs_refinement"] and p in captions]
        if to_fix:
            from concurrent.futures import ThreadPoolExecutor

            def _rewrite(platform):
                try:
                    return platform, r.caption_agent.refine_caption(
                        platform=platform,
                        original_caption=captions[platform]["primary_caption"],
                        reviewer_feedback=quality_evaluations[platform]["reviewer_feedback"],
                        brand_voice=brand_voice,
                        brand_profile_block=brand_profile_block,
                        language=language,
                    )
                except Exception as ref_err:  # keep the original caption
                    logger.warning(f"Rewrite for {platform} failed: {ref_err}")
                    return platform, None

            with ThreadPoolExecutor(max_workers=min(4, len(to_fix)), thread_name_prefix="fix") as pool:
                for platform, outcome in pool.map(_rewrite, to_fix):
                    if not outcome:
                        continue
                    refined_cap, ref_usage = outcome
                    refinements_count += 1
                    captions[platform]["primary_caption"] = refined_cap
                    captions[platform]["refined_by_critic"] = True
                    if ref_usage:
                        total_tokens += ref_usage.get("total_tokens", 0)
                        total_cost_usd += ref_usage.get("cost_usd", 0.0)
                    # Report the state after the fix, honestly
                    before = quality_evaluations[platform]
                    after = r.reviewer_agent.evaluate(
                        platform=platform,
                        caption=refined_cap,
                        hashtags=(hashtags.get(platform) or {}).get("hashtags", []),
                    )
                    after.pop("hashtags", None)
                    after["self_corrected"] = True
                    after["fixed_issues"] = before["issues"]
                    quality_evaluations[platform] = after

        checks_total = sum(q["checks_total"] for q in quality_evaluations.values())
        checks_passed = sum(q["checks_passed"] for q in quality_evaluations.values())
        _progress("reviewer", "done")
        rewrites = f"{refinements_count} caption rewrite{'s' if refinements_count != 1 else ''}"
        agents_executed.append(
            {
                "agent": "ReviewerAgent",
                "name": "Quality Checks",
                "role": f"{checks_passed}/{checks_total} checks passed (length, no CTAs/placeholders, "
                f"plain text, hashtag limits); {rewrites}",
                "status": "completed",
            }
        )

        _progress("guardrail", "active")
        # Step 7: Brand Guardrail - deterministic final scrub for the
        # brand-voice-persona-used-as-company-name failure mode (see
        # r._scrub_brand_voice_leak). Runs after refinement so it also
        # catches anything the critic's rewrite reintroduced.
        guardrail_corrections = 0
        for platform in platforms:
            cap = captions.get(platform)
            if not cap:
                continue
            fixed, changed = r._scrub_brand_voice_leak(cap.get("primary_caption", ""), brand_voice, brand_company_name)
            if changed:
                cap["primary_caption"] = fixed
                guardrail_corrections += 1

            tags = hashtags.get(platform)
            if tags:
                fixed_list = []
                for tag in tags.get("hashtags", []) or []:
                    fixed_tag, changed = r._scrub_brand_voice_leak(tag, brand_voice, brand_company_name)
                    if changed:
                        guardrail_corrections += 1
                    fixed_list.append(fixed_tag)
                # No spam tags (#follow4follow...), repeats or broken tags (services/hashtag_service.py)
                tags["hashtags"] = hashtag_service.tidy(fixed_list)
        _progress("guardrail", "done")

        agents_executed.append(
            {
                "agent": "BrandGuardrailAgent",
                "name": "Brand Guardrail",
                "role": (
                    f"Verified the brand-voice persona was never used as the company name ({guardrail_corrections} correction"
                    f"{'s' if guardrail_corrections != 1 else ''} applied)"
                    if guardrail_corrections
                    else "Verified the brand-voice persona was never used as the company name"
                ),
                "status": "completed",
            }
        )

        # Step 8: Compliance check against the user's confirmed industry/
        # market rules (services/compliance_service.py) - fixes what it can
        # in the caption and flags what needs a human. Skipped (no cost)
        # for users without a compliance profile.
        rules = r.active_rules_for_user(user_id)
        if rules:
            _progress("compliance", "active")
            flag_total = 0
            # Every platform's caption reviewed in one call (r.check_captions)
            try:
                checked, c_usage = r.check_captions(
                    {p: (captions.get(p) or {}).get("primary_caption", "") for p in platforms if captions.get(p)},
                    rules,
                    r.refine_llm,
                )
            except Exception as c_err:  # never fail a generation over the check
                logger.warning(f"[Compliance] Check failed: {c_err}")
                checked, c_usage = {}, {}
            total_tokens += c_usage.get("total_tokens", 0)
            total_cost_usd += c_usage.get("cost_usd", 0.0)
            _progress("compliance", "done")
            for platform, result in checked.items():
                if result:
                    captions[platform]["primary_caption"] = result.pop("caption")
                    compliance_results[platform] = result
                    flag_total += len(result["flags"])
            agents_executed.append(
                {
                    "agent": "ComplianceAgent",
                    "name": "Compliance Checker",
                    "role": f"Checked against {len(rules)} advertising rules for your industry and markets "
                    f"({flag_total} issue{'s' if flag_total != 1 else ''} found)",
                    "status": "completed",
                }
            )

    checks_total = sum(q.get("checks_total", 0) for q in quality_evaluations.values())
    checks_passed = sum(q.get("checks_passed", 0) for q in quality_evaluations.values())

    # Compile response
    response = {
        "success": True,
        "request_id": str(uuid.uuid4()),
        "story_analysis": story_analysis,
        "quality_summary": {
            "checks_passed": checks_passed,
            "checks_total": checks_total,
            "refinements_applied": refinements_count,
            "brand_voice_applied": brand_voice,
            "language": language,
        },
        "content": {},
        "agents_executed": agents_executed,
        "usage": {
            "total_tokens": total_tokens,
            "cost_usd": round(total_cost_usd, 6),
            "memories_referenced": len(retrieved_memories),
        },
    }

    # Image/video generation needs richer visual/factual grounding than the
    # short social caption text alone gives it - without this, media
    # generation was only ever told the caption, so the Research Summary's
    # imagery descriptions and research_notes never actually reached the
    # image/video prompt (same class of bug fixed earlier for the
    # competitor-dashboard's Strategy Synthesis image_prompt/video_prompt).
    media_prompt_parts = []
    if isinstance(story_analysis, dict):
        imagery_desc = story_analysis.get("imagery")
        if imagery_desc:
            media_prompt_parts.append("Visual elements to include: " + "; ".join(imagery_desc))
        research_notes_list = story_analysis.get("research_notes")
        if research_notes_list:
            # Conveyed visually, not printed: image models garble dense
            # small text, and printed stats/citations read as brand claims.
            media_prompt_parts.append(
                "Context to convey visually (do not print these figures or sources as text): "
                + "; ".join(research_notes_list[:3])
            )
    media_prompt_parts.append(r.IMAGE_TEXT_RULE)
    media_prompt_suffix = " ".join(media_prompt_parts)

    for platform in platforms:
        cap = captions.get(platform, {})
        primary_caption = cap.get("primary_caption", "")
        media_prompt = f"{primary_caption} {media_prompt_suffix}".strip() if media_prompt_suffix else primary_caption
        response["content"][platform] = {
            "caption": cap,
            "hashtags": hashtags.get(platform, {}),
            "strategy": strategies.get(platform, {}) if include_strategy else None,
            "quality": quality_evaluations.get(platform, {}),
            "media_prompt": media_prompt,
            "compliance": compliance_results.get(platform),
        }

    # Regenerate keeps the version's image (and video): a new caption doesn't
    # buy a new image. The user asks for a new image with the image's own
    # "New image" button or in chat.
    if data.get("keep_media_from_run_id") and r.DB_AVAILABLE and user_id is not None:
        try:
            kept = r.get_run_by_id(int(data["keep_media_from_run_id"]), user_id=user_id) or {}
            for platform, entry in (response.get("content") or {}).items():
                media = ((kept.get("content") or {}).get(platform) or {}).get("media") or {}
                keep = {
                    k: v for k, v in media.items() if k in ("image", "video") and isinstance(v, dict) and v.get("url")
                }
                if keep and isinstance(entry, dict):
                    entry["media"] = {**(entry.get("media") or {}), **keep}
        except (TypeError, ValueError) as keep_err:
            logger.warning(f"Could not keep media from run {data.get('keep_media_from_run_id')}: {keep_err}")

    # ── Persist to PostgreSQL ──────────────────────────────────────────
    run_id = None
    if r.DB_AVAILABLE and user_id is not None:
        try:
            content_to_save: dict[str, typing.Any] = dict(response["content"])
            content_to_save["_agents"] = agents_executed
            content_to_save["_quality"] = response["quality_summary"]
            if image_path and os.path.exists(image_path):
                content_to_save["_meta"] = {
                    "image_path": image_path,
                    "image_url": r._public_upload_url(image_path),
                }
            if data.get("version_of_run_id"):  # Regenerate: another version of that reply
                content_to_save.setdefault("_meta", {})["version_of"] = data.get("version_of_run_id")
            conversation_id = _conversation_for(user_id, data, story)
            run_id = r.save_run(
                story=story,
                tone=tone,
                platforms=platforms,
                content=content_to_save,
                user_id=user_id,
                tokens_used=total_tokens,
                cost_usd=round(total_cost_usd, 6),
                conversation_id=conversation_id,
            )
            response["run_id"] = run_id
            response["conversation_id"] = conversation_id
            r.log_event(
                "generate",
                conversation_id=conversation_id,
                run_id=run_id,
                user_id=user_id,
                platforms=platforms,
                provider="heyroute",
                status="completed",
                context_token_estimate=r.estimate_tokens(story_prompt, previous_context),
                retrieved_memory_count=len(retrieved_memories or []),
                latency_ms=int((time.time() - started) * 1000),
            )
        except Exception as db_err:
            logger.warning(f"[DB] Could not save run: {db_err}")

    # ── Store Run in ChromaDB Memory ───────────────────────────────────
    r.memory_service.store_campaign_run(
        run_id=run_id, story=story, content=response["content"], user_id=user_id, tone=tone, platforms=platforms
    )

    return response
