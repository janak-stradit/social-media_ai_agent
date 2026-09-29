"""Live smoke test for the HeyRoute integration (reasoning, image, video).

Run from the project root after adding the HEYROUTE_* keys to .env:

    python scripts/heyroute_smoke_test.py            # reasoning + image (cheap)
    python scripts/heyroute_smoke_test.py --video    # also one short video (billed per second)

Each check prints OK or the provider's exact error. Generated files land in
static/uploads/ like any other generation.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402


def check(name, fn):
    started = time.time()
    try:
        detail = fn()
        print(f"[OK]   {name} ({time.time() - started:.1f}s): {detail}")
    except Exception as err:  # noqa: BLE001 -- report and continue with the next check
        print(f"[FAIL] {name} ({time.time() - started:.1f}s): {err}")


def reasoning():
    import openai

    if not Config.HEYROUTE_API_KEY:
        raise RuntimeError("HEYROUTE_API_KEY not set")
    from services.llm_service import LLMService

    llm = LLMService()
    provider = next(p for p in llm.providers if p["name"] == "heyroute")
    client = openai.OpenAI(api_key=Config.HEYROUTE_API_KEY, base_url=Config.HEYROUTE_BASE_URL)
    response = client.chat.completions.create(
        **llm._chat_kwargs(provider, "Reply in one short sentence.", "Say hello from HeyRoute.", 0.7, 100)
    )
    return f"{Config.HEYROUTE_LLM_MODEL}: {llm._first_choice_text(response, 'heyroute').strip()[:120]}"


def reasoning_json():
    from services.llm_service import LLMService

    llm = LLMService()
    llm.providers = [p for p in llm.providers if p["name"] == "heyroute"]
    return llm.generate_json("Return JSON only.", 'Return {"ok": true, "model": "<your model name>"}.', max_tokens=100)


def image():
    from services.media_service import MediaGenerationService

    if not Config.HEYROUTE_IMAGE_API_KEY:
        raise RuntimeError("HEYROUTE_IMAGE_API_KEY not set")
    result = MediaGenerationService()._generate_image_heyroute(
        "A modern freight truck on a Dubai highway at golden hour, photorealistic", "linkedin", "1792x1024"
    )
    return f"{result['model']} -> {result['url']}"


def image_edit():
    from services.media_service import MediaGenerationService

    svc = MediaGenerationService()
    first = svc._generate_image_heyroute("A plain blue coffee mug on a white table", "instagram", "1024x1024")
    ref = os.path.join(Config.UPLOAD_FOLDER, os.path.basename(first["url"]))
    result = svc._generate_image_heyroute("Make the mug bright red; keep everything else", "instagram", "1024x1024", [ref, ref])
    return f"2 reference images -> {result['url']}"


def video():
    from services.media_service import MediaGenerationService

    if not Config.HEYROUTE_VIDEO_API_KEY:
        raise RuntimeError("HEYROUTE_VIDEO_API_KEY not set")
    result = MediaGenerationService()._generate_video_heyroute(
        "A freight truck driving along a desert highway at sunset, cinematic tracking shot", "linkedin"
    )
    return f"{result['provider']} -> {result['url']} (native audio: {result['has_native_audio']})"


if __name__ == "__main__":
    print(f"HeyRoute base URL: {Config.HEYROUTE_BASE_URL}")
    check("reasoning (chat completions)", reasoning)
    check("reasoning (JSON mode via LLMService)", reasoning_json)
    check("image (text-to-image)", image)
    check("image edit (2 reference images)", image_edit)
    if "--video" in sys.argv:
        check("video", video)
    else:
        print("[SKIP] video - run with --video (billed per second)")
