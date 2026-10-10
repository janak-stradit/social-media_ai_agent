"""
Media generation service.
- Image: Z.AI GLM-Image, OpenRouter Image API, or DALL-E 3 (OpenAI)
- Video: Z.AI CogVideoX (image-to-video), OpenRouter Video API
"""

import base64
import logging
import mimetypes
import os
import time
import typing
import uuid

import openai
import requests

from config import Config
from services import storage_service
from services.brand_logo_service import NO_AI_LOGO_RULE, overlay_logo
from services.llm_service import LLMService
from services.storage_service import mirror_to_s3

logger = logging.getLogger(__name__)

_PROMPT_LIMIT = 2000  # kie.ai's prompt cap (see _generate_image_kie)


def _strip_corner_patch(img):
    """Some HeyRoute images come back with a flat white rectangle pasted over
    one corner (where the image model's visible watermark sits). Trims the
    image just past it. Strict on purpose, so a genuinely white background
    (e.g. a catalog shot) is never trimmed: the block must be pure, flat white
    with clearly non-white image right next to it."""
    import statistics

    gray = img.convert("L")
    w, h = gray.size
    px = gray.load()
    for right in (True, False):
        for bottom in (True, False):
            cx, cy = (w - 3 if right else 2), (h - 3 if bottom else 2)  # just inside the edge (JPEG borders)
            step_x, step_y = (-1 if right else 1), (-1 if bottom else 1)
            x = cx
            while 0 < x < w - 1 and px[x, cy] > 245:
                x += step_x
            y = cy
            while 0 < y < h - 1 and px[cx, y] > 245:
                y += step_y
            bw, bh = abs(x - cx), abs(y - cy)
            if not (0.03 * w <= bw <= 0.25 * w and 0.03 * h <= bh <= 0.25 * h):
                continue
            xs = range(min(x, cx) + 1, max(x, cx)) if right else range(cx, x)
            ys = range(min(y, cy) + 1, max(y, cy)) if bottom else range(cy, y)
            inside = [px[i, j] for i in xs[::3] for j in ys[::3]]
            beside = [px[x + step_x * k, j] for k in range(1, 6) for j in ys[::3] if 0 <= x + step_x * k < w]
            above = [px[i, y + step_y * k] for k in range(1, 6) for i in xs[::3] if 0 <= y + step_y * k < h]
            if not inside or not beside or not above:
                continue
            if statistics.mean(inside) < 250 or statistics.pstdev(inside) > 4:
                continue
            if statistics.mean(beside) > 235 or statistics.mean(above) > 235:
                continue  # white continues past it: a real white background, leave it
            pad_x, pad_y = bw + 3, bh + 3
            box = (0 if right else pad_x, 0 if bottom else pad_y, w - pad_x if right else w, h - pad_y if bottom else h)
            logger.warning(f"Removed a {bw}x{bh} white patch from the {'bottom' if bottom else 'top'}-"
                           f"{'right' if right else 'left'} corner of a generated image")
            return img.crop(box)
    return img


# Reference images are sent as PNG or JPEG no larger than this on the long side
_REFERENCE_MAX_SIDE = 2048
_REFERENCE_MAX_BYTES = 8 * 1024 * 1024
_REFERENCE_MIME = {"PNG": "image/png", "JPEG": "image/jpeg"}


def _reference_upload(path: str) -> tuple[str, bytes, str]:
    """(file name, bytes, MIME type) of a reference image in a form every
    image model accepts. The upstream providers reject (400 invalid_request)
    a file whose label doesn't match its content - e.g. a JPEG saved under a
    .png name, or a .webp that Windows' mimetypes can't name - as well as GIFs
    and very large images. PNG/JPEG files that are fine are sent unchanged;
    anything else is converted (PNG when it has transparency, else JPEG)."""
    import io

    from PIL import Image

    stem = os.path.splitext(os.path.basename(path))[0] or "reference"
    with open(path, "rb") as f:
        data = f.read()
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            if fmt in _REFERENCE_MIME and max(img.size) <= _REFERENCE_MAX_SIDE and len(data) <= _REFERENCE_MAX_BYTES:
                return f"{stem}.{'png' if fmt == 'PNG' else 'jpg'}", data, _REFERENCE_MIME[fmt]
            img.seek(0)  # first frame of an animated GIF/WebP
            alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
            img = img.convert("RGBA" if alpha else "RGB")
            img.thumbnail((_REFERENCE_MAX_SIDE, _REFERENCE_MAX_SIDE), Image.LANCZOS)
            out = io.BytesIO()
            if alpha:
                img.save(out, format="PNG", optimize=True)
                return f"{stem}.png", out.getvalue(), "image/png"
            img.save(out, format="JPEG", quality=92)
            return f"{stem}.jpg", out.getvalue(), "image/jpeg"
    except Exception as err:  # noqa: BLE001 - not an image Pillow can read: send it as it is
        logger.warning(f"Could not prepare reference image {os.path.basename(path)}: {err}")
        return os.path.basename(path), data, mimetypes.guess_type(path)[0] or "application/octet-stream"


def _image_extension(img_data: bytes) -> str:
    """The real file extension of image bytes (providers return PNG, JPEG or WebP)."""
    head = img_data[:12]
    if head.startswith(b"\xff\xd8"):
        return ".jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def _image_price(model: str | None) -> float:
    """Price per image for `model` from Admin -> Image Settings (falls back to
    HEYROUTE_IMAGE_COST_USD when the database isn't available)."""
    try:
        from db import image_model_price

        return image_model_price(model)
    except Exception:  # noqa: BLE001
        return Config.HEYROUTE_IMAGE_COST_USD


def _with_no_logo_rule(prompt: str) -> str:
    """Every image prompt ends with the no-AI-logo rule (the real logo is
    stamped on afterwards) - trimmed so the rule itself survives the cap."""
    if NO_AI_LOGO_RULE in prompt:
        return prompt
    return f"{prompt[: _PROMPT_LIMIT - len(NO_AI_LOGO_RULE) - 2]}\n\n{NO_AI_LOGO_RULE}"


class MediaGenerationService:
    """Generates social media images and videos via Z.AI, OpenRouter, or OpenAI."""

    OPENROUTER_BASE = "https://openrouter.ai/api/v1"
    IMAGE_SIZES = {
        "instagram": "1024x1024",
        "facebook": "1792x1024",
        "linkedin": "1792x1024",
    }

    def __init__(self):
        self.llm_service = LLMService()
        api_key = Config.OPENAI_API_KEY
        self.api_key = api_key
        self.zai_api_key = Config.Z_AI_API_KEY

        # Force Bedrock provider strictly as requested by the user
        self.media_provider = "bedrock"
        self.uses_bedrock = False
        self.uses_zai = False
        self.uses_openrouter = False

        self.zai_client = None
        if self.zai_api_key:
            self.zai_client = openai.OpenAI(
                api_key=self.zai_api_key,
                base_url=Config.Z_AI_BASE_URL,
            )

        self.client = None
        if api_key:
            if self.uses_openrouter:
                self.client = openai.OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
            elif self.uses_zai:
                self.client = self.zai_client
            else:
                self.client = openai.OpenAI(api_key=api_key)
        elif self.uses_zai:
            self.client = self.zai_client

        # Initialize AWS Bedrock and S3 Clients if Bedrock is selected
        self.bedrock_client = None
        self.s3_client = None
        if self.uses_bedrock:
            try:
                import boto3
                from botocore.config import Config as BotoConfig

                aws_access_key = getattr(Config, "AWS_ACCESS_KEY_ID", None)
                aws_secret_key = getattr(Config, "AWS_SECRET_ACCESS_KEY", None)
                aws_profile = getattr(Config, "AWS_PROFILE", None)
                aws_region = getattr(Config, "AWS_REGION", "us-east-1")

                session_kwargs = {}
                if aws_profile:
                    session_kwargs["profile_name"] = aws_profile
                elif aws_access_key and aws_secret_key:
                    session_kwargs["aws_access_key_id"] = aws_access_key
                    session_kwargs["aws_secret_access_key"] = aws_secret_key

                if aws_region:
                    session_kwargs["region_name"] = aws_region

                session = boto3.Session(**session_kwargs)

                boto_config = BotoConfig(read_timeout=300, connect_timeout=60, retries={"max_attempts": 3})

                self.bedrock_client = session.client("bedrock-runtime", config=boto_config)
                self.s3_client = session.client("s3")
            except Exception as e:
                logger.warning(f"Bedrock client initialization failed: {e}")

        self.upload_folder = Config.UPLOAD_FOLDER
        os.makedirs(self.upload_folder, exist_ok=True)

    def _openrouter_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _zai_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.zai_api_key}",
            "Content-Type": "application/json",
            "Accept-Language": "en-US,en",
        }

    def _zai_base(self) -> str:
        return Config.Z_AI_BASE_URL.rstrip("/")

    def _raise_api_error(self, response: requests.Response, provider: str = "API") -> None:
        try:
            err = response.json()
            message = err.get("message") or err.get("error", {}).get("message") or err.get("error") or response.text
        except ValueError:
            message = response.text
        raise RuntimeError(f"{provider} error ({response.status_code}): {message}")

    def _zai_video_size(self, platform: str) -> str:
        return {
            "instagram": "720x1280",
            "facebook": "1280x720",
            "linkedin": "1280x720",
        }.get(platform, "1280x720")

    def _zai_image_size(self, platform: str) -> str:
        if Config.Z_AI_IMAGE_MODEL.startswith("cogview"):
            return {
                "instagram": "1024x1024",
                "facebook": "1440x720",
                "linkedin": "1440x720",
            }.get(platform, "1024x1024")
        return {
            "instagram": "1280x1280",
            "facebook": "1728x960",
            "linkedin": "1728x960",
        }.get(platform, "1280x1280")

    def _zai_video_duration(self) -> int:
        return 10 if Config.VIDEO_DURATION > 5 else 5

    def _poll_zai_async(self, task_id: str, result_key: str) -> dict:
        """Poll Z.AI async-result until SUCCESS or FAIL."""
        deadline = time.time() + Config.VIDEO_POLL_TIMEOUT
        status_data = {}

        while time.time() < deadline:
            poll = requests.get(
                f"{self._zai_base()}/async-result/{task_id}",
                headers=self._zai_headers(),
                timeout=60,
            )
            if not poll.ok:
                self._raise_api_error(poll, "Z.AI")

            status_data = poll.json()
            task_status = status_data.get("task_status")
            if task_status == "SUCCESS":
                return status_data
            if task_status == "FAIL":
                raise RuntimeError(status_data.get("message") or "Z.AI generation failed")

            time.sleep(Config.VIDEO_POLL_INTERVAL)

        raise RuntimeError(f"Z.AI {result_key} generation timed out. Try again later.")

    def _build_zai_video_payload(self, prompt: str, platform: str, image_path: str) -> dict:
        """Build video request payload for CogVideoX or Vidu models."""
        model = Config.Z_AI_VIDEO_MODEL
        data_uri = self._image_to_data_uri(image_path)

        if model.startswith("vidu"):
            payload: dict[str, typing.Any] = {
                "model": model,
                "prompt": prompt[:512],
                "image_url": data_uri,
                "with_audio": True,
                "movement_amplitude": "auto",
            }
            if model == "vidu2-image":
                payload["duration"] = 4
                payload["size"] = "1280x720"
            else:
                payload["duration"] = 5
                payload["size"] = "1920x1080"
            return payload

        return {
            "model": model,
            "prompt": prompt[:512],
            "image_url": [data_uri],
            "quality": Config.Z_AI_VIDEO_QUALITY,
            "with_audio": True,
            "size": self._zai_video_size(platform),
            "fps": Config.Z_AI_VIDEO_FPS,
            "duration": self._zai_video_duration(),
        }

    def _zai_video_duration_from_payload(self, payload: dict) -> int:
        return payload.get("duration", self._zai_video_duration())

    def _zai_video_resolution_from_payload(self, payload: dict) -> str:
        return payload.get("size", self._zai_video_size("facebook"))

    def _generate_image_zai(self, prompt: str, platform: str) -> dict:
        """Generate via Z.AI /images/generations."""
        size = self._zai_image_size(platform)
        payload = {
            "model": Config.Z_AI_IMAGE_MODEL,
            "prompt": prompt,
            "size": size,
        }
        if Config.Z_AI_IMAGE_MODEL.startswith("glm-image"):
            payload["quality"] = Config.Z_AI_IMAGE_QUALITY

        response = requests.post(
            f"{self._zai_base()}/images/generations",
            headers=self._zai_headers(),
            json=payload,
            timeout=180,
        )
        if not response.ok:
            self._raise_api_error(response, "Z.AI")

        data = response.json()
        items = data.get("data") or []
        if not items or not items[0].get("url"):
            raise RuntimeError("Z.AI returned no image data")

        image_url = items[0]["url"]
        img_data = requests.get(image_url, timeout=60).content
        local_filename, _ = self._save_image_bytes(img_data, platform)
        return {
            "url": f"/static/uploads/{local_filename}",
            "original_url": image_url,
            "prompt": prompt,
        }

    def _generate_video_zai(self, prompt: str, platform: str, image_path: str) -> dict:
        """Submit Z.AI image-to-video job, poll, and save MP4 locally."""
        payload = self._build_zai_video_payload(prompt, platform, image_path)
        duration = self._zai_video_duration_from_payload(payload)
        resolution = self._zai_video_resolution_from_payload(payload)

        submit = requests.post(
            f"{self._zai_base()}/videos/generations",
            headers=self._zai_headers(),
            json=payload,
            timeout=60,
        )
        if not submit.ok:
            self._raise_api_error(submit, "Z.AI")

        job = submit.json()
        task_id = job.get("id")
        if not task_id:
            raise RuntimeError("Z.AI did not return a video task ID")

        if job.get("task_status") == "SUCCESS":
            status_data = job
        else:
            status_data = self._poll_zai_async(task_id, "video")

        video_results = status_data.get("video_result") or []
        if not video_results or not video_results[0].get("url"):
            raise RuntimeError("Z.AI returned no video data")

        video_url = video_results[0]["url"]
        video_data = requests.get(video_url, timeout=180).content
        if not video_data:
            raise RuntimeError("Failed to download video from Z.AI")

        local_filename = self._save_video_bytes(video_data, platform)
        return {
            "url": f"/static/uploads/{local_filename}",
            "prompt": prompt,
            "duration": duration,
            "resolution": resolution,
            "model": Config.Z_AI_VIDEO_MODEL,
        }

    def _raise_openrouter_error(self, response: requests.Response) -> None:
        try:
            err = response.json()
            message = err.get("error", {}).get("message") or err.get("error") or response.text
        except ValueError:
            message = response.text
        raise RuntimeError(f"Error code: {response.status_code} - {message}")

    def _save_video_bytes(self, video_data: bytes, platform: str) -> str:
        local_filename = f"gen_{platform}_{uuid.uuid4().hex[:8]}.mp4"
        local_path = os.path.join(self.upload_folder, local_filename)
        with open(local_path, "wb") as f:
            f.write(video_data)
        return local_filename

    def _resolve_image_path(self, image_path: str | None) -> str | None:
        if not image_path:
            return None
        if os.path.isabs(image_path) and os.path.exists(image_path):
            return image_path
        if os.path.exists(image_path):
            return image_path
        candidate = os.path.join(Config.UPLOAD_FOLDER, os.path.basename(image_path))
        if os.path.exists(candidate):
            return candidate
        # Not on this server's disk (e.g. a rebuilt instance) - fetch the S3 copy
        return storage_service.ensure_local(storage_service.UPLOAD_URL_PREFIX + os.path.basename(image_path))

    def _resolve_image_paths(self, image_path: str | list[str] | None) -> list[str]:
        """Normalizes the single-image-or-list reference param (multiple
        brand assets, e.g. Aiden + the StradIT logo, can be combined into one
        generation) into a list of resolved, existing local file paths."""
        candidates = image_path if isinstance(image_path, list) else ([image_path] if image_path else [])
        resolved = [self._resolve_image_path(p) for p in candidates]
        return [p for p in resolved if p]

    def _image_to_data_uri(self, image_path: str) -> str:
        resolved = self._resolve_image_path(image_path)
        if not resolved:
            raise RuntimeError(f"Reference image not found: {image_path}")
        mime, _ = mimetypes.guess_type(resolved)
        mime = mime or "image/jpeg"
        with open(resolved, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        return f"data:{mime};base64,{encoded}"

    def _clean_motion_text(self, text: str) -> str:
        """Strip non-visual meta instructions, quotes, dialogue scripts, and hashtags from video prompts."""
        import re

        if not text:
            return ""
        # Remove dialogue instruction quotes and negative directives
        cleaned = re.sub(
            r'Speak\s+exactly\s+this\s+dialogue\s+only[:\s]*["“].*?["”]', "", text, flags=re.IGNORECASE | re.DOTALL
        )
        cleaned = re.sub(r'Do\s+not\s+(?:say|repeat).*?["“].*?["”]', "", cleaned, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r'["“].*?["”]', "", cleaned)  # remove quoted text
        cleaned = re.sub(r"#\w+", "", cleaned)  # remove hashtags
        cleaned = re.sub(r"https?://\S+", "", cleaned)  # remove URLs
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    _VIDEO_PROMPT_LIMIT = 1500
    # Aspect ratio per platform (width, height); anything else is 16:9.
    _VIDEO_ASPECTS = {"instagram": (9, 16), "facebook": (16, 9), "linkedin": (16, 9)}

    def _build_video_prompt(
        self, caption: str, platform: str, tone: str | None = None, has_reference_image: bool = False
    ) -> str:
        """The video model's prompt: the brief's own scene description (the
        strategy's video_prompt when the caller has one, else the caption)
        with the platform's style. The scene is never swapped for a stock one."""
        platform_style = {
            "instagram": "vibrant vertical video, modern style",
            "facebook": "warm, polished brand video",
            "linkedin": "premium, clean, professional aesthetic",
        }.get(platform, "professional and cinematic")

        scene = self._clean_motion_text(caption) or "A polished brand promo scene"
        if len(scene) > self._VIDEO_PROMPT_LIMIT:
            # Cut at the last full sentence that fits
            cut = scene[: self._VIDEO_PROMPT_LIMIT]
            scene = cut[: cut.rfind(". ") + 1] or cut
        reference = (
            "Start from the provided image as the first frame and keep its subject, colors and composition. "
            if has_reference_image
            else ""
        )
        return (
            f"{reference}{scene.rstrip('.')}. "
            f"Style: {platform_style}. Smooth natural motion, cinematic lighting, high-fidelity render."
        )

    def _generate_video_openrouter(self, prompt: str, platform: str, image_path: str | None = None) -> dict:
        """Submit an OpenRouter video job, poll until done, and save the MP4 locally."""
        aspect_ratio_map = {
            "instagram": "9:16",
            "facebook": "16:9",
            "linkedin": "16:9",
        }
        payload: dict[str, typing.Any] = {
            "model": Config.VIDEO_MODEL,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio_map.get(platform, "16:9"),
            "duration": Config.VIDEO_DURATION,
            "resolution": Config.VIDEO_RESOLUTION,
            "generate_audio": True,
        }

        resolved_image = self._resolve_image_path(image_path)
        if resolved_image:
            payload["frame_images"] = [
                {
                    "type": "image_url",
                    "image_url": {"url": self._image_to_data_uri(resolved_image)},
                    "frame_type": "first_frame",
                }
            ]

        submit = requests.post(
            f"{self.OPENROUTER_BASE}/videos",
            headers=self._openrouter_headers(),
            json=payload,
            timeout=60,
        )
        if not submit.ok:
            self._raise_openrouter_error(submit)

        job = submit.json()
        job_id = job.get("id")
        polling_url = job.get("polling_url") or f"{self.OPENROUTER_BASE}/videos/{job_id}"
        if not job_id:
            raise RuntimeError("OpenRouter did not return a video job ID")

        deadline = time.time() + Config.VIDEO_POLL_TIMEOUT
        status_data = job
        while time.time() < deadline:
            status = status_data.get("status")
            if status == "completed":
                break
            if status in {"failed", "cancelled", "expired"}:
                raise RuntimeError(status_data.get("error") or f"Video generation {status}")

            time.sleep(Config.VIDEO_POLL_INTERVAL)
            poll = requests.get(polling_url, headers=self._openrouter_headers(), timeout=60)
            if not poll.ok:
                self._raise_openrouter_error(poll)
            status_data = poll.json()

        if status_data.get("status") != "completed":
            raise RuntimeError("Video generation timed out. Try again or use a shorter duration.")

        video_data = None
        for url in status_data.get("unsigned_urls") or []:
            download = requests.get(url, headers=self._openrouter_headers(), timeout=180)
            if download.ok and download.content:
                video_data = download.content
                break

        if not video_data:
            content = requests.get(
                f"{self.OPENROUTER_BASE}/videos/{job_id}/content",
                headers=self._openrouter_headers(),
                timeout=180,
            )
            if not content.ok:
                self._raise_openrouter_error(content)
            video_data = content.content

        if not video_data:
            raise RuntimeError("OpenRouter returned no video data")

        local_filename = self._save_video_bytes(video_data, platform)
        return {
            "url": f"/static/uploads/{local_filename}",
            "prompt": prompt,
            "duration": Config.VIDEO_DURATION,
            "resolution": Config.VIDEO_RESOLUTION,
            "model": Config.VIDEO_MODEL,
            "cost": (status_data.get("usage") or {}).get("cost"),
        }

    # HeyRoute returns 2048px PNGs (~4 MB) whatever size is requested; social
    # posts don't need more than this, so images are stored downscaled as JPEG.
    _STORED_MAX_SIDE = {"square": 1080, "landscape": 1600}

    def _save_compact_image(self, img_data: bytes, platform: str, max_side: int) -> str:
        """Saves the image downscaled to max_side as a high-quality JPEG (a
        few hundred KB instead of ~4 MB). Falls back to the original bytes."""
        import io

        from PIL import Image

        try:
            with Image.open(io.BytesIO(img_data)) as img:
                img = _strip_corner_patch(img.convert("RGB"))
                img.thumbnail((max_side, max_side), Image.LANCZOS)
                local_filename = f"gen_{platform}_{uuid.uuid4().hex[:8]}.jpg"
                img.save(os.path.join(self.upload_folder, local_filename), format="JPEG", quality=90, optimize=True)
                return local_filename
        except Exception as err:  # never lose the image over compression
            logger.warning(f"Could not compress image, keeping original: {err}")
            return self._save_image_bytes(img_data, platform)[0]

    def _fit_to_aspect(self, local_path: str, aspect: str) -> tuple[int, int]:
        """Crops (centre) and resizes a saved image to the exact size for an
        aspect ratio - e.g. 1080x1920 for a 9:16 story, which Meta requires.
        The image model only treats the requested shape as a hint. Runs
        before the logo is stamped, so the logo is never cut off."""
        from PIL import Image

        from services.image_presets import ASPECT_SIZES

        target_w, target_h = ASPECT_SIZES[aspect]
        with Image.open(local_path) as img:
            img = img.convert("RGB")
            w, h = img.size
            target_ratio, ratio = target_w / target_h, w / h
            if abs(ratio - target_ratio) / target_ratio > 0.02:
                if ratio > target_ratio:  # too wide: trim the sides
                    new_w = round(h * target_ratio)
                    left = (w - new_w) // 2
                    img = img.crop((left, 0, left + new_w, h))
                else:  # too tall: trim top and bottom
                    new_h = round(w / target_ratio)
                    top = (h - new_h) // 2
                    img = img.crop((0, top, w, top + new_h))
            img = img.resize((target_w, target_h), Image.LANCZOS)
            img.save(local_path, format="JPEG", quality=90, optimize=True)
        return target_w, target_h

    @mirror_to_s3
    def create_preset_image(
        self, prompt: str, image_path: str | None, aspect: str, logo_path: str | None = None, model: str | None = None,
        require_reference: bool = True,
    ) -> dict:
        """One image for a Studio Chat image command (/3dbillboard, /metaad, ...):
        the product photo is the reference, the result is cropped to the exact
        size for `aspect`, then the real logo is stamped on when logo_path is set.
        require_reference=False (/carousel): drawn from the prompt alone when
        no photo is attached."""
        if getattr(Config, "USE_MOCK_LLM", False):
            return self._generate_mock_media("instagram", "image", prompt)
        references = self._resolve_image_paths(image_path)
        if not references and (require_reference or image_path):
            return {"success": False, "type": "image", "error": "The product photo could not be found - attach it again."}
        try:
            result = self._generate_image_primary(_with_no_logo_rule(prompt), "preset", "1024x1024", references or None,
                                                  model=model)
            local = os.path.join(self.upload_folder, os.path.basename(result["url"]))
            width, height = self._fit_to_aspect(local, aspect)
        except Exception as e:  # noqa: BLE001 - reported to the user per image
            return {"success": False, "type": "image", "error": str(e)}
        return {
            "success": True,
            "type": "image",
            "url": result["url"],
            "prompt": result["prompt"],
            "size": f"{width}x{height}",
            "width": width,
            "height": height,
            "aspect": aspect,
            "cost": result.get("cost", 0.0),
            "model": result.get("model"),
            "model_id": result.get("model_id"),
            **self._stamp_logo(result["url"], logo_path),
        }

    def _save_image_bytes(self, img_data: bytes, platform: str) -> tuple[str, str]:
        local_filename = f"gen_{platform}_{uuid.uuid4().hex[:8]}{_image_extension(img_data)}"
        local_path = os.path.join(self.upload_folder, local_filename)
        with open(local_path, "wb") as f:
            f.write(img_data)
        return local_filename, local_path

    def _generate_image_openrouter(self, prompt: str, platform: str, size: str, image_path: str | None = None) -> dict:
        """Generate via OpenRouter's dedicated /v1/images API."""
        # The default routed model (openai/gpt-image-1) only accepts
        # 1:1, 3:2, 2:3, auto - "16:9" is rejected outright (400). 3:2 is the
        # closest landscape approximation it actually supports.
        aspect_ratio_map = {
            "instagram": "1:1",
            "facebook": "3:2",
            "linkedin": "3:2",
        }
        payload: dict[str, typing.Any] = {
            "model": Config.IMAGE_MODEL,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio_map.get(platform, "1:1"),
            "size": size,
            "quality": "medium",
            "output_format": "png",
            "n": 1,
        }

        resolved_image = self._resolve_image_path(image_path)
        if resolved_image:
            payload["input_references"] = [
                {
                    "type": "image_url",
                    "image_url": {"url": self._image_to_data_uri(resolved_image)},
                }
            ]

        response = requests.post(
            "https://openrouter.ai/api/v1/images",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )

        if not response.ok:
            try:
                err = response.json()
                message = err.get("error", {}).get("message") or err.get("error") or response.text
            except ValueError:
                message = response.text
            raise RuntimeError(f"Error code: {response.status_code} - {message}")

        data = response.json()
        item = data["data"][0]

        if item.get("b64_json"):
            img_data = base64.b64decode(item["b64_json"])
            local_filename, _ = self._save_image_bytes(img_data, platform)
            return {
                "url": f"/static/uploads/{local_filename}",
                "original_url": None,
                "prompt": prompt,
            }

        if item.get("url"):
            image_url = item["url"]
            img_data = requests.get(image_url, timeout=30).content
            local_filename, _ = self._save_image_bytes(img_data, platform)
            return {
                "url": f"/static/uploads/{local_filename}",
                "original_url": image_url,
                "prompt": prompt,
            }

        raise RuntimeError("OpenRouter returned no image data")

    def _upload_reference_to_kie(self, api_key: str, local_path: str) -> str | None:
        """Uploads a local image to kie.ai's own temporary file host so it
        gets a real fetchable URL - kie.ai's image_urls input requires an
        actual URL its servers can reach, not a data: URI, and our images are
        only served locally. Returns the public downloadUrl, or None on
        failure (caller falls back to text-to-image rather than hard-failing)."""
        try:
            with open(local_path, "rb") as f:
                upload_resp = requests.post(
                    "https://kieai.redpandaai.co/api/file-stream-upload",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": (os.path.basename(local_path), f)},
                    data={"uploadPath": "character-references"},
                    timeout=30,
                )
            if not upload_resp.ok:
                logger.warning(
                    f"kie.ai file upload failed: {upload_resp.status_code} - {upload_resp.text[:200]}"
                )
                return None
            return ((upload_resp.json() or {}).get("data") or {}).get("downloadUrl")
        except Exception as e:
            logger.warning(f"kie.ai file upload error: {e}")
            return None

    # ── HeyRoute (https://heyroute.ai/v1, OpenAI-compatible) ─────────────────
    # Image: gemini-3-pro-image via /images/generations (text-to-image) or
    # /images/edits (with reference images). n must be 1 (the Gemini models
    # return 400 otherwise) and size is ignored, so the aspect ratio goes in the
    # prompt. "stream": false asks for a plain JSON body; an SSE body (the
    # endpoint's default) is still parsed if the gateway sends one anyway.
    # Video: /videos creates a task, /videos/{id} is polled, and the finished
    # file is downloaded from /videos/{id}/content with the same key.

    _HEYROUTE_ASPECT_HINTS = {
        "instagram": "Square 1:1 aspect ratio.",
        "facebook": "Landscape 16:9 aspect ratio.",
        "linkedin": "Landscape 16:9 aspect ratio.",
    }
    _HEYROUTE_VIDEO_RATIOS = {"instagram": "9:16", "facebook": "16:9", "linkedin": "16:9"}
    _HEYROUTE_MAX_REFERENCES = 14  # HeyRoute's documented limit for Gemini edits

    def _heyroute_headers(self, key: str) -> dict:
        return {"Authorization": f"Bearer {key}"}

    @staticmethod
    def _heyroute_image_payload(resp) -> dict:
        """The {"data": [...]} payload from a JSON or streamed (SSE) images
        response. Streams differ by model: "event: completed" with the whole
        payload, or OpenAI-style "image_generation.completed" events carrying
        b64_json directly. Partial-image events are skipped."""
        content_type = resp.headers.get("Content-Type", "")
        if "text/event-stream" not in content_type and not resp.text.lstrip().startswith(("event:", "data:")):
            return resp.json()
        import json as _json

        def normalized(obj: dict) -> dict:
            return obj if obj.get("data") else {"data": [{"b64_json": obj.get("b64_json"), "url": obj.get("url")}]}

        event, final = None, None
        for line in resp.text.splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
                continue
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if not data or data == "[DONE]":
                continue
            try:
                obj = _json.loads(data)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            kind = f"{event or ''} {obj.get('type') or ''}"
            if "error" in kind or obj.get("error"):
                raise RuntimeError(f"HeyRoute image error: {data[:300]}")
            if "partial" in kind:
                continue
            if obj.get("data") or obj.get("b64_json") or obj.get("url"):
                final = normalized(obj)
                if "completed" in kind:
                    return final
        if final:
            return final
        raise RuntimeError("HeyRoute image stream ended without an image.")

    @staticmethod
    def _heyroute_error_text(resp) -> str:
        """A readable reason for a failed image request - never a Cloudflare
        HTML error page dumped into the chat."""
        status = resp.status_code
        if status in (522, 524, 504):
            return f"HeyRoute took too long to create the image (timeout {status}). Please try again."
        if status in (502, 503, 520, 521, 523):
            return f"HeyRoute's image service is temporarily unavailable ({status}). Please try again in a minute."
        text = resp.text or ""
        if "<html" in text[:300].lower():
            return f"HeyRoute image request failed ({status})."
        if status == 400 and "invalid_request" in text:
            import re

            ref = re.search(r"request id: ([A-Za-z0-9]+)", text)
            return (
                "The image service rejected this request (400 invalid_request). Usually the prompt or the "
                "attached image was refused - try rewording the request or attaching a different image."
                + (f" Reference: {ref.group(1)}" if ref else "")
            )
        return f"HeyRoute image request failed: {status} - {text[:300]}"

    def _generate_image_heyroute(
        self,
        prompt: str,
        platform: str,
        size: str,
        image_path: str | list[str] | None = None,
        square: bool = False,
        model: str | None = None,
    ) -> dict:
        """Same return shape as _generate_image_kie: {url (local), original_url,
        prompt, model, cost}."""
        import base64

        key = Config.HEYROUTE_IMAGE_API_KEY
        if not key:
            raise RuntimeError("HEYROUTE_IMAGE_API_KEY is not configured.")
        # The user's model from Image access / Image Settings, else the global default
        model = model or Config.HEYROUTE_IMAGE_MODEL
        # square=True: one image shared by every platform of a post (1:1 works on all)
        hint = self._HEYROUTE_ASPECT_HINTS["instagram"] if square else self._HEYROUTE_ASPECT_HINTS.get(platform)
        full_prompt = f"{prompt}\n\n{hint}" if hint and hint not in prompt else prompt
        references = self._resolve_image_paths(image_path)[: self._HEYROUTE_MAX_REFERENCES]
        uploads = [_reference_upload(ref) for ref in references]
        base = Config.HEYROUTE_BASE_URL

        def send(plain: bool = False):
            # Streamed: HeyRoute is behind Cloudflare, which cuts a reply that
            # hasn't started within 100 s (HTTP 524) - slow images (detailed
            # scenes, pro models) used to fail that way. A stream starts at once.
            # plain=True: just model + prompt. Some models (e.g. gpt-image-2.5)
            # reject the extra n/stream fields with 400 invalid_request.
            extra = {} if plain else {"n": 1, "stream": True}
            if references:
                return requests.post(
                    f"{base}/images/edits",
                    headers=self._heyroute_headers(key),
                    data={"model": model, "prompt": full_prompt, **{k: str(v).lower() for k, v in extra.items()}},
                    files=[("image", upload) for upload in uploads],
                    timeout=300,
                )
            return requests.post(
                f"{base}/images/generations",
                headers={**self._heyroute_headers(key), "Content-Type": "application/json"},
                json={"model": model, "prompt": full_prompt, **extra},
                timeout=300,
            )

        resp = send()
        if resp.status_code == 400 and "invalid_request" in resp.text:
            resp = send(plain=True)  # rejected requests make no image, so this costs nothing extra
        if not resp.ok:
            # The user's model can't be used right now - not enabled on the image
            # key (404 model_not_found) or its provider is down (502/503/504).
            # Nothing was generated, so make the image with the default model
            # instead of failing the user.
            # A request the user's model rejects outright (400 invalid_request,
            # even without the extra fields) gets the same treatment.
            unusable = (
                (resp.status_code == 404 and "model_not_found" in resp.text)
                or (resp.status_code == 400 and "invalid_request" in resp.text)
                or resp.status_code in (502, 503, 504)
            )
            if unusable and model != Config.HEYROUTE_IMAGE_MODEL:
                logger.warning(f"HeyRoute image model {model!r} unavailable ({resp.status_code}); "
                      f"using {Config.HEYROUTE_IMAGE_MODEL!r} instead")
                return self._generate_image_heyroute(
                    prompt, platform, size, image_path, square=square, model=Config.HEYROUTE_IMAGE_MODEL
                )
            logger.warning(
                f"HeyRoute image request failed: {resp.status_code} model={model} "
                f"references={[(name, len(data), mime) for name, data, mime in uploads]} "
                f"prompt_chars={len(full_prompt)} - {resp.text[:300]!r}"
            )
            raise RuntimeError(self._heyroute_error_text(resp))

        item = ((self._heyroute_image_payload(resp) or {}).get("data") or [{}])[0]
        original_url = item.get("url")
        if item.get("b64_json"):
            img_data = base64.b64decode(item["b64_json"])
        elif original_url:
            img_data = requests.get(original_url, timeout=60).content
        else:
            raise RuntimeError("HeyRoute image response had no image data.")

        is_square = square or platform == "instagram"
        local_filename = self._save_compact_image(
            img_data, platform, self._STORED_MAX_SIDE["square" if is_square else "landscape"]
        )
        return {
            "url": f"/static/uploads/{local_filename}",
            "original_url": original_url,
            "prompt": full_prompt,
            "model": f"heyroute/{model}",
            "model_id": model,
            "cost": _image_price(model),
        }

    def _generate_image_primary(
        self,
        prompt: str,
        platform: str,
        size: str,
        image_path: str | list[str] | None = None,
        square: bool = False,
        model: str | None = None,
    ) -> dict:
        """The default image provider: HeyRoute only (HEYROUTE_IMAGE_API_KEY),
        like video and text. No kie.ai fallback - a fallback failure used to
        replace HeyRoute's real error, so the user saw a misleading kie.ai
        message. Other providers are only used when chosen explicitly via
        generate_image(ai_model=...)."""
        if not Config.HEYROUTE_IMAGE_API_KEY:
            raise RuntimeError("HEYROUTE_IMAGE_API_KEY is not configured - image generation runs on HeyRoute.")
        return self._generate_image_heyroute(prompt, platform, size, image_path, square=square, model=model)

    def _file_to_data_uri(self, path: str) -> str:
        mime = mimetypes.guess_type(path)[0] or "image/jpeg"
        with open(path, "rb") as f:
            return f"data:{mime};base64," + base64.b64encode(f.read()).decode()

    def _heyroute_video_task(self, key: str, body: dict) -> bytes:
        """Create a HeyRoute video task, poll it to completion and return the
        MP4 bytes. Raises on failure or timeout (never resubmits: every
        submission is billed separately)."""
        base = Config.HEYROUTE_BASE_URL
        headers = self._heyroute_headers(key)
        created = requests.post(f"{base}/videos", headers={**headers, "Content-Type": "application/json"},
                                json=body, timeout=60)
        if not created.ok:
            raise RuntimeError(f"HeyRoute video create failed ({body.get('model')}): "
                               f"{created.status_code} - {created.text[:300]}")
        task_id = (created.json() or {}).get("task_id") or (created.json() or {}).get("id")
        if not task_id:
            raise RuntimeError(f"HeyRoute video create returned no task id: {created.text[:300]}")

        timeout = self._heyroute_video_timeout(body.get("model") or "")
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(15)
            task = requests.get(f"{base}/videos/{task_id}", headers=headers, timeout=60).json() or {}
            status = task.get("status")
            if status == "completed":
                video = requests.get(f"{base}/videos/{task_id}/content", headers=headers, timeout=600)
                video.raise_for_status()
                return video.content
            if status == "failed":
                raise RuntimeError(f"HeyRoute video failed ({body.get('model')}): "
                                   f"{(task.get('error') or {}).get('message', 'task failed')}")
        raise RuntimeError(f"HeyRoute video timed out after {timeout}s (task {task_id}).")

    # HeyRoute video models differ in what they accept (help -> Generate videos):
    #   grok-video          6 / 10 / 15 s only; no ratio, resolution or reference image; silent
    #   grok-imagine-video* 1-15 s; ratio, resolution, first-frame image; own soundtrack
    #   minimax-h3-*        4-15 s (quantized tier: 4-10); always its own frame (768p is
    #                       1376x768 landscape - ratio is ignored); exactly ONE reference
    #                       item; own soundtrack (video + audio in one pass); a clip
    #                       takes well over ten minutes
    @staticmethod
    def _heyroute_video_family(model: str) -> str:
        if model.startswith("minimax-h3"):
            return "minimax"
        return "grok-imagine" if model.startswith("grok-imagine") else "grok-video"

    @classmethod
    def _heyroute_makes_audio(cls, model: str) -> bool:
        """Models that return video + audio in one file: they are given the narration in the prompt."""
        return cls._heyroute_video_family(model) != "grok-video"

    @staticmethod
    def _video_has_audio(path: str) -> bool:
        """True when the MP4 at `path` really carries an audio track."""
        try:
            from moviepy.video.io.VideoFileClip import VideoFileClip

            with VideoFileClip(path) as clip:
                return clip.audio is not None
        except Exception:  # noqa: BLE001 - unreadable file: treat as silent
            return False

    @classmethod
    def _heyroute_takes_reference(cls, model: str) -> bool:
        return cls._heyroute_video_family(model) != "grok-video"

    @classmethod
    def _heyroute_video_timeout(cls, model: str) -> int:
        """HeyRoute: allow at least 30 minutes for the minimax-h3 tiers."""
        slow = cls._heyroute_video_family(model) == "minimax"
        return max(Config.HEYROUTE_VIDEO_TIMEOUT, 1800) if slow else Config.HEYROUTE_VIDEO_TIMEOUT

    def _heyroute_video_body(self, model: str, prompt: str, platform: str, image_path: str | None) -> dict:
        family = self._heyroute_video_family(model)
        seconds = max(1, min(15, int(Config.HEYROUTE_VIDEO_SECONDS)))
        if family == "grok-video":
            # Anything but 6 / 10 / 15 seconds is a 400
            return {"model": model, "prompt": prompt,
                    "seconds": str(min((6, 10, 15), key=lambda s: abs(s - seconds)))}
        if family == "minimax":
            body = {"model": model, "prompt": prompt,
                    "seconds": str(max(4, min(10 if "quantized" in model else 15, seconds)))}
        else:
            body = {
                "model": model,
                "prompt": prompt,
                "seconds": str(seconds),
                "ratio": self._HEYROUTE_VIDEO_RATIOS.get(platform, "16:9"),
                "resolution": Config.HEYROUTE_VIDEO_RESOLUTION,
            }
        resolved = self._resolve_image_path(image_path)
        if resolved and os.path.exists(resolved):
            body["input_reference"] = self._file_to_data_uri(resolved)  # one item, never an array
        return body

    def _generate_video_heyroute(self, prompt: str, platform: str, image_path: str | None = None) -> dict:
        """HEYROUTE_VIDEO_MODEL, then
        HEYROUTE_VIDEO_FALLBACK_MODEL if one is set. Same return shape as
        _generate_google_gemini_video. Raises if every attempt fails."""
        key = Config.HEYROUTE_VIDEO_API_KEY
        if not key:
            raise RuntimeError("HEYROUTE_VIDEO_API_KEY is not configured.")

        models = [m for m in (Config.HEYROUTE_VIDEO_MODEL, Config.HEYROUTE_VIDEO_FALLBACK_MODEL) if m]
        attempts = [self._heyroute_video_body(m, prompt, platform, image_path) for m in models]

        last_error = None
        for body in attempts:
            try:
                logger.info(f"Generating video via HeyRoute {body['model']}...")
                content = self._heyroute_video_task(key, body)
                filename = f"heyroute_video_{uuid.uuid4().hex[:8]}.mp4"
                with open(os.path.join(self.upload_folder, filename), "wb") as f:
                    f.write(content)
                # Whether the model's own soundtrack is there is read off the file, not assumed
                native_audio = self._video_has_audio(os.path.join(self.upload_folder, filename))
                if not native_audio:
                    logger.warning(f"HeyRoute {body['model']} returned a video with no audio track.")
                return {
                    "success": True,
                    "url": f"/static/uploads/{filename}",
                    "prompt": prompt,
                    "model": body["model"],
                    "provider": f"HeyRoute ({body['model']})",
                    "duration": int(body["seconds"]),
                    "cost": round(int(body["seconds"]) * Config.HEYROUTE_VIDEO_COST_PER_SECOND_USD, 6),
                    "has_native_audio": native_audio,
                    "audio_mode": "single_pass_native" if native_audio else "none",
                }
            except Exception as err:
                last_error = err
                logger.warning(f"HeyRoute {body['model']} failed: {err}")
        raise RuntimeError(f"HeyRoute video generation failed: {last_error}")

    def _generate_image_kie(
        self, prompt: str, platform: str, size: str, image_path: str | list[str] | None = None
    ) -> dict:
        """Generate via kie.ai's Google Nano Banana model - the base
        text-to-image model, or the "edit" variant (image-to-image) when a
        reference image is given.

        kie.ai's job API is async: createTask returns a taskId immediately,
        the actual image is only ready once recordInfo reports state=success.
        callBackUrl is intentionally omitted - it requires a public endpoint
        kie.ai's servers can reach, which this app doesn't have in local/dev
        - polling recordInfo works everywhere instead.
        """
        import json

        api_key = getattr(Config, "KIE_API_KEY", None) or os.getenv("KIE_API_KEY")
        if not api_key:
            raise RuntimeError("KIE_API_KEY is not configured.")

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        aspect_ratio_map = {
            "instagram": "1:1",
            "facebook": "16:9",
            "linkedin": "16:9",
        }

        model = "google/nano-banana"
        task_input: dict[str, typing.Any] = {
            "prompt": prompt[:2000],
            "output_format": "png",
            "aspect_ratio": aspect_ratio_map.get(platform, "1:1"),
        }

        resolved_images = self._resolve_image_paths(image_path)
        if resolved_images:
            reference_urls = [
                url for url in (self._upload_reference_to_kie(api_key, p) for p in resolved_images) if url
            ]
            if reference_urls:
                model = "google/nano-banana-edit"
                task_input["image_urls"] = reference_urls

        def _create_and_poll() -> str | None:
            """Runs one createTask + poll cycle. Returns the result URL, or
            None if the task timed out without reaching state=success (a
            transient stall, not necessarily a real failure - kie.ai
            occasionally never advances a task past queuing/generating)."""
            create_resp = requests.post(
                "https://api.kie.ai/api/v1/jobs/createTask",
                headers=headers,
                json={"model": model, "input": task_input},
                timeout=30,
            )
            if not create_resp.ok:
                raise RuntimeError(f"kie.ai createTask failed: {create_resp.status_code} - {create_resp.text[:300]}")

            task_id = ((create_resp.json() or {}).get("data") or {}).get("taskId")
            if not task_id:
                raise RuntimeError(f"kie.ai createTask returned no taskId: {create_resp.text[:300]}")

            # Nano Banana generations are typically fast; poll for up to ~60s.
            for _ in range(30):
                time.sleep(2)
                poll_resp = requests.get(
                    "https://api.kie.ai/api/v1/jobs/recordInfo",
                    headers=headers,
                    params={"taskId": task_id},
                    timeout=30,
                )
                poll_resp.raise_for_status()
                poll_data = (poll_resp.json() or {}).get("data") or {}
                state = poll_data.get("state")

                if state == "success":
                    result = json.loads(poll_data.get("resultJson") or "{}")
                    urls = result.get("resultUrls") or []
                    return urls[0] if urls else None
                if state == "fail":
                    raise RuntimeError(f"kie.ai generation failed: {poll_data.get('failMsg') or 'Unknown error'}")
                # waiting / queuing / generating - keep polling
            return None

        # A stalled task (never reaching success/fail within the poll window) is
        # transient often enough that one retry with a brand-new task clears it,
        # rather than surfacing "variation N failed" to the user immediately.
        result_url = _create_and_poll()
        if not result_url:
            logger.warning("kie.ai task timed out - retrying once with a new task...")
            result_url = _create_and_poll()

        if not result_url:
            raise RuntimeError("kie.ai task timed out or returned no result URL.")

        img_data = requests.get(result_url, timeout=30).content
        local_filename, _ = self._save_image_bytes(img_data, platform)
        return {
            "url": f"/static/uploads/{local_filename}",
            "original_url": result_url,
            "prompt": prompt,
            "model": model,
            "cost": 0.02,
        }

    # ── Image Generation ───────────────────────────────────────────────────
    def _parse_size(self, size_str: str) -> tuple[int, int]:
        try:
            w_str, h_str = size_str.split("x")
            w, h = int(w_str), int(h_str)
            # Map to Amazon Nova Canvas supported dimensions (1024x1024, 1280x720, 720x1280)
            if w > h:
                return 1280, 720
            elif h > w:
                return 720, 1280
            else:
                return 1024, 1024
        except Exception:
            return 1024, 1024

    def _get_image_as_jpeg_base64(self, image_path: str, target_size: tuple[int, int] | None = None) -> str:
        import io

        from PIL import Image

        resolved = self._resolve_image_path(image_path)
        if not resolved:
            raise RuntimeError(f"Reference image not found: {image_path}")

        with Image.open(resolved) as img:
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGB")
            if target_size:
                resample = getattr(Image, "Resampling", None)
                # Pillow version feature-detection (Resampling enum vs. old ANTIALIAS constant).
                # pylint: disable-next=using-constant-test
                resample_method = resample.LANCZOS if resample else getattr(Image, "ANTIALIAS", 3)
                img = img.resize(target_size, resample_method)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=90)
            return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def _generate_image_bedrock(self, prompt: str, platform: str, size: str, image_path: str | None = None) -> dict:
        """Generate an image using AWS Bedrock (low cost model, e.g. Amazon Nova Canvas)."""
        if not self.bedrock_client:
            raise RuntimeError("AWS Bedrock client is not initialized. Check AWS credentials.")

        model_id = getattr(Config, "BEDROCK_IMAGE_MODEL", "amazon.nova-canvas-v1:0")
        width, height = self._parse_size(size)
        import json
        import random

        # Creative-variation seed, not security-sensitive.
        seed = random.randint(0, 2147483646)  # nosec B311

        resolved_image = self._resolve_image_path(image_path)

        # Truncate prompt to 1000 chars maximum for Nova Canvas
        prompt_text = prompt[:1000]

        if resolved_image:
            try:
                input_image_b64 = self._get_image_as_jpeg_base64(resolved_image, target_size=(width, height))
                payload = {
                    "taskType": "IMAGE_VARIATION",
                    "imageVariationParams": {
                        "images": [input_image_b64],
                        "text": prompt_text,
                        "similarityStrength": 0.7,
                    },
                    "imageGenerationConfig": {
                        "numberOfImages": 1,
                        "quality": "standard",
                        "height": height,
                        "width": width,
                        "seed": seed,
                    },
                }
                response = self.bedrock_client.invoke_model(
                    body=json.dumps(payload),
                    modelId=model_id,
                    accept="application/json",
                    contentType="application/json",
                )
            except Exception as variation_err:
                logger.warning(
                    f"Bedrock IMAGE_VARIATION payload notice: {variation_err}. Falling back to TEXT_IMAGE taskType..."
                )
                payload = {
                    "taskType": "TEXT_IMAGE",
                    "textToImageParams": {"text": prompt_text},
                    "imageGenerationConfig": {
                        "numberOfImages": 1,
                        "quality": "standard",
                        "height": height,
                        "width": width,
                        "seed": seed,
                    },
                }
                response = self.bedrock_client.invoke_model(
                    body=json.dumps(payload),
                    modelId=model_id,
                    accept="application/json",
                    contentType="application/json",
                )
        else:
            payload = {
                "taskType": "TEXT_IMAGE",
                "textToImageParams": {"text": prompt_text},
                "imageGenerationConfig": {
                    "numberOfImages": 1,
                    "quality": "standard",
                    "height": height,
                    "width": width,
                    "seed": seed,
                },
            }
            response = self.bedrock_client.invoke_model(
                body=json.dumps(payload), modelId=model_id, accept="application/json", contentType="application/json"
            )

        response_body = json.loads(response.get("body").read())
        images = response_body.get("images") or []
        if not images:
            raise RuntimeError("AWS Bedrock returned no image data")

        img_data = base64.b64decode(images[0])
        local_filename, _ = self._save_image_bytes(img_data, platform)
        return {
            "url": f"/static/uploads/{local_filename}",
            "original_url": None,
            "prompt": prompt,
            "cost": 0.03,
            "model": model_id,
        }

    def _generate_video_bedrock(self, prompt: str, platform: str, image_path: str | None = None) -> dict:
        """Generate a video using AWS Bedrock (low cost model, e.g. Amazon Nova Reel)."""
        if not self.bedrock_client or not self.s3_client:
            raise RuntimeError("AWS Bedrock or S3 client is not initialized. Check AWS credentials.")

        # Amazon Nova Reel prompts must be strictly <= 512 characters
        prompt = prompt[:512]

        model_id = getattr(Config, "BEDROCK_VIDEO_MODEL", "amazon.nova-reel-v1:0")
        s3_bucket = getattr(Config, "AWS_S3_BUCKET", None)
        if not s3_bucket:
            raise RuntimeError(
                "AWS_S3_BUCKET is not configured in environment variables. Bedrock video generation requires S3."
            )

        import random

        # Creative-variation seed, not security-sensitive.
        seed = random.randint(0, 2147483646)  # nosec B311
        resolved_image = self._resolve_image_path(image_path)

        dimension = "1280x720"
        if resolved_image:
            input_image_b64 = self._get_image_as_jpeg_base64(resolved_image, target_size=(1280, 720))
            model_input = {
                "taskType": "TEXT_VIDEO",
                "textToVideoParams": {
                    "text": prompt,
                    "images": [{"format": "jpeg", "source": {"bytes": input_image_b64}}],
                },
                "videoGenerationConfig": {"fps": 24, "durationSeconds": 6, "dimension": dimension, "seed": seed},
            }
        else:
            model_input = {
                "taskType": "TEXT_VIDEO",
                "textToVideoParams": {"text": prompt},
                "videoGenerationConfig": {"fps": 24, "durationSeconds": 6, "dimension": dimension, "seed": seed},
            }

        job_id = uuid.uuid4().hex
        s3_uri = f"s3://{s3_bucket.strip('/')}/bedrock-video-outputs/{job_id}/"

        output_config = {"s3OutputDataConfig": {"s3Uri": s3_uri}}
        bucket_owner = getattr(Config, "AWS_BUCKET_OWNER", None)
        if bucket_owner:
            output_config["s3OutputDataConfig"]["bucketOwner"] = str(bucket_owner)

        response = self.bedrock_client.start_async_invoke(
            clientRequestToken=str(uuid.uuid4()),
            modelId=model_id,
            modelInput=model_input,
            outputDataConfig=output_config,
        )

        invocation_arn = response["invocationArn"]

        # Poll for completion
        deadline = time.time() + Config.VIDEO_POLL_TIMEOUT
        final_s3_uri = None

        while time.time() < deadline:
            poll_resp = self.bedrock_client.get_async_invoke(invocationArn=invocation_arn)
            status = poll_resp.get("status")
            if status == "Completed":
                final_s3_uri = poll_resp["outputDataConfig"]["s3OutputDataConfig"]["s3Uri"]
                break
            elif status == "Failed":
                failure_msg = poll_resp.get("failureMessage", "Unknown Bedrock async failure")
                raise RuntimeError(f"Bedrock video generation failed: {failure_msg}")

            time.sleep(Config.VIDEO_POLL_INTERVAL)
        else:
            raise RuntimeError("Bedrock video generation timed out.")

        # Download output from S3
        from urllib.parse import urlparse

        parsed = urlparse(final_s3_uri)
        bucket = parsed.netloc
        prefix = parsed.path.lstrip("/")

        # List objects under prefix to find the mp4 file
        list_resp = self.s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        contents = list_resp.get("Contents", [])

        video_key = None
        for item in contents:
            key = item["Key"]
            if key.endswith(".mp4"):
                video_key = key
                break

        if not video_key:
            video_key = f"{prefix.rstrip('/')}/output.mp4"

        obj_resp = self.s3_client.get_object(Bucket=bucket, Key=video_key)
        video_data = obj_resp["Body"].read()

        local_filename = self._save_video_bytes(video_data, platform)

        return {
            "url": f"/static/uploads/{local_filename}",
            "prompt": prompt,
            "duration": 6,
            "resolution": "1280x720",
            "model": model_id,
            "cost": 0.08,
        }

    def _generate_mock_media(self, platform: str, media_type: str, caption: str) -> dict:
        """Generate a local visual mock asset for offline testing."""
        import uuid

        from PIL import Image, ImageDraw

        filename = f"mock_{media_type}_{uuid.uuid4().hex[:8]}.png"
        filepath = os.path.join(self.upload_folder, filename)

        w, h = (1024, 1024) if media_type == "image" else (1280, 720)
        img = Image.new("RGB", (w, h), color=(30, 41, 59))
        draw = ImageDraw.Draw(img)

        # Draw stylish mock border and graphic elements
        draw.rectangle([30, 30, w - 30, h - 30], outline=(16, 185, 129), width=5)
        draw.ellipse([w // 4, h // 4, 3 * w // 4, 3 * h // 4], outline=(37, 99, 235), width=4)

        img.save(filepath, format="PNG")

        return {
            "success": True,
            "type": media_type,
            "platform": platform,
            "url": f"/static/uploads/{filename}",
            "original_url": None,
            "prompt": caption or "Mock visual asset placeholder",
            "size": f"{w}x{h}",
            "provider": "mock",
            "cost": 0.0,
            "model": "mock-media-v1",
        }

    def _generate_mock_video(self, platform: str, caption: str) -> dict:
        """A real (2 s, plain) MP4 for offline testing, so the page's video
        player has something it can play."""
        import subprocess

        import imageio_ffmpeg

        aspect_w, aspect_h = self._VIDEO_ASPECTS.get(platform, (16, 9))
        w, h = (1280, 720) if aspect_w > aspect_h else (720, 1280)
        filename = f"mock_video_{uuid.uuid4().hex[:8]}.mp4"
        filepath = os.path.join(self.upload_folder, filename)
        try:
            subprocess.run(
                [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi",
                 "-i", f"color=c=0x1e293b:s={w}x{h}:d=2", "-pix_fmt", "yuv420p", filepath],
                check=True, timeout=60,
            )
        except Exception as err:
            return {"success": False, "type": "video", "platform": platform, "error": f"Mock video failed: {err}"}
        return {
            "success": True,
            "type": "video",
            "platform": platform,
            "url": f"/static/uploads/{filename}",
            "prompt": caption or "Mock video placeholder",
            "duration": 2,
            "resolution": f"{w}x{h}",
            "provider": "mock",
            "cost": 0.0,
            "model": "mock-media-v1",
        }

    def _video_resolution(self, url: str | None) -> str | None:
        """ "WxH" of a saved video, or None when it can't be read."""
        path = self._resolve_image_path(url)
        if not path or not os.path.exists(path):
            return None
        try:
            from moviepy.video.io.VideoFileClip import VideoFileClip

            with VideoFileClip(path) as clip:
                return f"{clip.w}x{clip.h}"
        except Exception:
            return None

    def _generate_image_openai(self, prompt: str, platform: str, size: str) -> dict:
        """Generate an image using OpenAI DALL-E 3."""
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        # DALL-E 3 only supports 1024x1024, 1024x1792, or 1792x1024
        w, h = self._parse_size(size)
        if w > h:
            oai_size = "1792x1024"
        elif h > w:
            oai_size = "1024x1792"
        else:
            oai_size = "1024x1024"

        if not self.client:
            raise RuntimeError("OpenAI client is not initialized")

        response = self.client.images.generate(
            model="dall-e-3",
            prompt=prompt[:4000],
            size=oai_size,
            quality="standard",
            n=1,
        )
        if not response or not response.data:
            raise RuntimeError("Failed to generate image: OpenAI returned no valid data")

        image_url = response.data[0].url
        if not image_url:
            raise RuntimeError("Failed to generate image: OpenAI returned no URL")
        img_data = requests.get(image_url, timeout=30).content
        local_filename, _ = self._save_image_bytes(img_data, platform)
        return {
            "url": f"/static/uploads/{local_filename}",
            "original_url": image_url,
            "prompt": prompt,
            "cost": 0.040,
            "model": "dall-e-3",
        }

    # ── Image Generation ───────────────────────────────────────────────────
    # The linter flags that not every path through this function returns a dict (some fall
    # through, implicitly returning None). Worth tracing properly; not done as part of lint adoption.
    @mirror_to_s3
    def generate_image(  # pylint: disable=inconsistent-return-statements
        self,
        caption: str,
        platform: str,
        tone: str | None = None,
        image_path: str | list[str] | None = None,
        ai_model: str = "kie",
        logo_path: str | None = None,
        square: bool = False,
        model: str | None = None,
    ) -> dict:
        """
        Generate a social media image. The model is told never to draw a
        logo; when logo_path is given, the real logo is stamped on afterwards
        (see _stamp_logo) and the result also carries clean_url.
        Returns: { url, local_path, prompt, size, platform }
        """
        if caption and ("CONTENT GENERATION BLOCKED" in caption or "No Strong Match" in caption):
            return {
                "success": False,
                "type": "image",
                "platform": platform,
                "error": "CONTENT GENERATION BLOCKED. Reason: No Strong Match was identified between this competitor topic and the available projects.",
            }

        if getattr(Config, "USE_MOCK_LLM", False):
            logger.info("USE_MOCK_LLM is enabled. Generating mock image asset...")
            return self._generate_mock_media(platform, "image", caption)

        # square: one 1:1 image reused by all of a post's platforms
        size = "1024x1024" if square else self.IMAGE_SIZES.get(platform, "1024x1024")

        resolved_references = self._resolve_image_paths(image_path)
        has_reference = bool(resolved_references)
        # Providers other than kie.ai only support a single reference image.
        single_reference = resolved_references[0] if resolved_references else None

        if has_reference:
            if (
                len(caption) > 150
                or "midjourney" in caption.lower()
                or "prompt" in caption.lower()
                or "slide" in caption.lower()
            ):
                prompt = caption
                prompt += "\n\nCRITICAL: Use the provided reference image for the character's exact facial features, hair, skin tone, and visual identity. The character in the image MUST look exactly like the reference image."
            else:
                platform_style = {
                    "instagram": "vibrant, modern style, portrait orientation, highly polished",
                    "facebook": "warm and inviting, polished and clean look, corporate sharing",
                    "linkedin": "corporate executive, clean design, high-end business style",
                }.get(platform, "professional and engaging")
                tone_hint = f", {tone} tone" if tone else ""
                headline = self._extract_headline(caption)
                prompt = (
                    f"Create a professional social media image for {platform.capitalize()} based on the uploaded reference image. "
                    f"Preserve the main subject's exact facial features, hair, skin tone, and visual identity from the reference image. "
                    f"Brief: {caption[:200]}. "
                    f"Style: {platform_style}{tone_hint}. "
                    f'Render the bold headline text "{headline}" in large clean sans-serif typography, high contrast against '
                    f"the background, positioned so it does not cover the subject's face. Do not add any other text, "
                    f"captions, or watermarks. {NO_AI_LOGO_RULE} "
                    f"Premium quality, highly detailed."
                )
        else:
            # If the user provides a detailed prompt (like a Midjourney prompt), use it directly
            if len(caption) > 150 or "midjourney" in caption.lower() or "prompt" in caption.lower():
                prompt = caption
            else:
                prompt = self._enhance_image_prompt(caption, platform, tone)

        prompt = _with_no_logo_rule(prompt)
        try:
            if ai_model == "google_gemini":
                result = self._generate_google_gemini_image(prompt, platform, size, single_reference)
            elif ai_model == "openai":
                result = self._generate_image_openai(prompt, platform, size)
            elif ai_model == "bedrock":
                result = self._generate_image_bedrock(prompt, platform, size, single_reference)
            elif ai_model == "zai":
                result = self._generate_image_zai(prompt, platform)
            elif ai_model == "openrouter":
                result = self._generate_image_openrouter(prompt, platform, size, single_reference)
            elif ai_model == "kie":
                # The default ("kie" is the frontend's historical name for it): HeyRoute
                result = self._generate_image_primary(
                    prompt, platform, size, resolved_references, square=square, model=model
                )
            else:
                # Default to pollinations
                result = self._generate_pollinations_image(prompt, platform, size)
        except Exception as e:
            return {
                "success": False,
                "type": "image",
                "platform": platform,
                "error": str(e),
            }

        return {
            "success": True,
            "type": "image",
            "platform": platform,
            "url": result["url"],
            "original_url": result.get("original_url"),
            "prompt": result["prompt"],
            "size": size,
            "provider": result.get("model", "bedrock"),
            "cost": result.get("cost", 0.03),
            "model": result.get("model", "bedrock"),
            **self._stamp_logo(result["url"], logo_path),
        }

    def _stamp_logo(self, url: str, logo_path: str | None) -> dict:
        """Stamps the real logo onto a generated image (brand_logo_service),
        keeping an unbranded copy - {"clean_url": ...} - so a follow-up edit
        works from the clean image instead of redrawing/duplicating the logo.
        {} when there's no logo or stamping failed (image left as-is)."""
        if not logo_path or not url:
            return {}
        import shutil

        local = os.path.join(self.upload_folder, os.path.basename(url))
        if not os.path.exists(local):
            return {}
        stem, ext = os.path.splitext(os.path.basename(url))
        clean_name = f"{stem}_clean{ext}"
        shutil.copyfile(local, os.path.join(self.upload_folder, clean_name))
        if not overlay_logo(local, logo_path):
            return {}
        return {"clean_url": f"/static/uploads/{clean_name}", "logo_applied": True}

    @mirror_to_s3
    def edit_image(
        self,
        prompt: str,
        platform: str,
        image_path: str | list[str] | None = None,
        logo_path: str | None = None,
        model: str | None = None,
        aspect: str | None = None,
    ) -> dict:
        """
        Surgical follow-up edit of an existing image (Studio Chat refinement).
        aspect ("9:16", ...): keep an image command's exact size (see _fit_to_aspect).
        Unlike generate_image(), the prompt is sent verbatim - no headline or
        "preserve facial features" wrapping - so a precise edit instruction
        ("replace the headline with exactly ...") isn't diluted. Uses kie.ai's
        edit model with the previous image as reference; with no usable
        reference it generates from the prompt instead.
        Returns the same shape as generate_image().
        """
        if getattr(Config, "USE_MOCK_LLM", False):
            return self._generate_mock_media(platform, "image", prompt)

        size = self.IMAGE_SIZES.get(platform, "1024x1024")
        try:
            result = self._generate_image_primary(
                _with_no_logo_rule(prompt), platform, size, self._resolve_image_paths(image_path), model=model
            )
            if aspect:
                width, height = self._fit_to_aspect(os.path.join(self.upload_folder, os.path.basename(result["url"])), aspect)
                size = f"{width}x{height}"
        except Exception as e:
            return {"success": False, "type": "image", "platform": platform, "error": str(e)}

        return {
            "success": True,
            "type": "image",
            "platform": platform,
            "url": result["url"],
            "original_url": result.get("original_url"),
            "prompt": result["prompt"],
            "size": size,
            "provider": result.get("model"),
            "cost": result.get("cost", 0.02),
            "model": result.get("model"),
            **self._stamp_logo(result["url"], logo_path),
        }

    @mirror_to_s3
    def generate_carousel_images(
        self, image_prompt: str, platform: str, reference_image_path: str | list[str] | None = None
    ) -> list[dict]:
        """Splits a multi-slide carousel image_prompt (as produced by
        StoryAgent.generate_channel_storyline, format: "Slide N (Title): description")
        into its individual slide descriptions and generates one distinct
        image per slide via kie.ai - instead of regenerating near-identical
        "variations" from a single short caption, which is why repeated
        generations kept coming out with the same background/composition.
        Each slide naturally looks different since it describes a different
        scene (hook / problem / solution / outcome), while sharing the
        prompt's own "Overall Aesthetic/Style" line for a cohesive carousel look.
        """
        import re

        if not image_prompt:
            return []

        style_match = re.search(r"Overall Aesthetic/Style:\s*(.+?)(?=\n\s*Slide\s+\d+|\Z)", image_prompt, re.DOTALL)
        overall_style = style_match.group(1).strip() if style_match else ""

        slide_matches = list(
            re.finditer(r"Slide\s+(\d+)\s*\(([^)]+)\):\s*(.+?)(?=\n\s*Slide\s+\d+\s*\(|\Z)", image_prompt, re.DOTALL)
        )
        slides = (
            [(int(m.group(1)), m.group(2).strip(), m.group(3).strip()) for m in slide_matches]
            if slide_matches
            else [(1, "Single Image", image_prompt)]
        )

        results = []
        for slide_num, slide_title, slide_desc in slides:
            prompt_parts = []
            if overall_style:
                prompt_parts.append(f"Overall style: {overall_style}.")
            prompt_parts.append(f"Slide {slide_num} ({slide_title}): {slide_desc}")
            if reference_image_path:
                prompt_parts.append(
                    "Preserve the main subject's exact facial features, hair, skin tone, and visual "
                    "identity from the uploaded reference image."
                )
            prompt_parts.append(NO_AI_LOGO_RULE)
            slide_prompt = " ".join(prompt_parts)[:2000]

            try:
                result = self._generate_image_primary(slide_prompt, platform, "1792x1024", reference_image_path)
                result["success"] = True
                result["slide_number"] = slide_num
                result["slide_title"] = slide_title
            except Exception as e:
                logger.warning(f"Carousel slide {slide_num} ({slide_title}) generation failed: {e}")
                result = {"success": False, "slide_number": slide_num, "slide_title": slide_title, "error": str(e)}
            results.append(result)

        return results

    def _generate_google_gemini_image(
        self, prompt: str, platform: str, size: str, image_path: str | None = None
    ) -> dict:
        """Generate an image using Google Gemini (Imagen 3) API via google-genai SDK."""
        import os

        from config import Config

        google_key = getattr(Config, "GOOGLE_API_KEY", None) or os.getenv("GOOGLE_API_KEY")
        if not google_key:
            raise RuntimeError("GOOGLE_API_KEY is missing in your environment or config file.")

        try:
            import google.genai as genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("The 'google-genai' package is required. Run 'pip install google-genai'.") from exc

        client = genai.Client(api_key=google_key)

        aspect_ratio = "1:1"
        if platform in ["facebook", "linkedin"]:
            aspect_ratio = "16:9"
        elif platform == "instagram":
            aspect_ratio = "1:1"

        logger.info(f"Generating image via Google Gemini (imagen-3.0-generate-001) for {platform}...")

        # Gemini does not natively support an image_path for image generation in this SDK endpoint currently,
        # so we rely purely on the text prompt
        result = client.models.generate_images(
            model="imagen-3.0-generate-001",
            prompt=prompt[:2000],
            config=types.GenerateImagesConfig(
                number_of_images=1, output_mime_type="image/jpeg", aspect_ratio=aspect_ratio
            ),
        )

        if not result.generated_images:
            raise RuntimeError("Google Gemini image generation returned empty result.")

        img_bytes = result.generated_images[0].image.image_bytes
        local_filename, _ = self._save_image_bytes(img_bytes, platform)

        return {
            "url": f"/static/uploads/{local_filename}",
            "prompt": prompt,
            "cost": 0.03,
            "model": "imagen-3.0-generate-001",
            "provider": "Google Gemini",
        }

    def _generate_google_gemini_video(self, prompt: str, platform: str, image_path: str | None = None) -> dict:
        """Generate a video using Google Gemini / Veo Video Generation API via google-genai SDK."""
        google_key = getattr(Config, "GOOGLE_API_KEY", None) or os.getenv("GOOGLE_API_KEY")
        if not google_key:
            raise RuntimeError("GOOGLE_API_KEY is missing in your environment or config file.")

        try:
            import google.genai as genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("The 'google-genai' package is required. Run 'pip install google-genai'.") from exc

        model_name = getattr(Config, "GEMINI_VIDEO_MODEL", "veo-3.1-generate-preview")
        logger.info(f"Initiating Google Gemini Video generation with model: {model_name}...")

        client = genai.Client(api_key=google_key)
        aspect_ratio = "9:16" if platform == "instagram" else "16:9"
        # Veo accepts only 4 / 6 / 8 seconds -- snap the configured value to one.
        veo_seconds = min((4, 6, 8), key=lambda s: abs(s - int(getattr(Config, "GEMINI_VIDEO_DURATION", 8))))

        gen_kwargs = {
            "model": model_name,
            "prompt": prompt[:512],
            "config": types.GenerateVideosConfig(  # pylint: disable=no-member
                aspect_ratio=aspect_ratio,
                duration_seconds=veo_seconds,
                number_of_videos=1,
                generate_audio=getattr(Config, "GENERATE_NATIVE_AUDIO", True),
            ),
        }

        resolved_image = self._resolve_image_path(image_path)
        if resolved_image and os.path.exists(resolved_image):
            try:
                with open(resolved_image, "rb") as f:
                    img_bytes = f.read()
                gen_kwargs["image"] = types.Image(image_bytes=img_bytes, mime_type="image/jpeg")
            except Exception as img_err:
                logger.warning(f"Warning loading image for Gemini Video: {img_err}")

        native_audio_requested = gen_kwargs["config"].generate_audio
        try:
            operation = client.models.generate_videos(**gen_kwargs)  # pylint: disable=no-member
        except Exception as gen_err:
            # "generate_audio" is an Enterprise-only Veo parameter - a Developer
            # API key rejects the call outright rather than just ignoring it,
            # so retry once without requesting native audio instead of failing
            # the whole video generation over an audio feature we can't use.
            if native_audio_requested and "generate_audio" in str(gen_err):
                logger.warning("generate_audio not supported on this Gemini API tier - retrying without it...")
                native_audio_requested = False
                gen_kwargs["config"] = types.GenerateVideosConfig(  # pylint: disable=no-member
                    aspect_ratio=aspect_ratio,
                    duration_seconds=veo_seconds,
                    number_of_videos=1,
                )
                operation = client.models.generate_videos(**gen_kwargs)  # pylint: disable=no-member
            else:
                raise

        logger.info("Polling Google Gemini Video operation (Native Single-Pass Video + Audio)...")
        deadline = time.time() + 300
        while not operation.done and time.time() < deadline:
            time.sleep(8)
            operation = client.operations.get(operation)  # pylint: disable=no-member

        if not operation.done:
            raise RuntimeError("Google Gemini Video generation operation timed out after 300s.")

        result = operation.result
        if not result or not getattr(result, "generated_videos", None):
            raise RuntimeError("Google Gemini Video generation returned empty result.")

        generated_video = result.generated_videos[0]
        if not generated_video.video:
            raise RuntimeError("Google Gemini Video generation returned empty video content.")
        filename = f"gemini_video_{uuid.uuid4().hex[:8]}.mp4"
        filepath = os.path.join(self.upload_folder, filename)

        # Flagged as an unexpected kwarg for the installed google-genai SDK version; unverified
        # without a real Google GenAI credential to exercise this path against.
        # pylint: disable-next=unexpected-keyword-arg
        client.files.download(file=generated_video.video, destination=filepath)
        return {
            "success": True,
            "url": f"/static/uploads/{filename}",
            "prompt": prompt,
            "model": model_name,
            "provider": "Google Gemini (Veo)",
            "has_native_audio": native_audio_requested,
            "audio_mode": "single_pass_native" if native_audio_requested else "none",
        }

    def _generate_pollinations_image(self, prompt: str, platform: str, size: str) -> dict:
        import os
        import secrets
        import time
        import urllib.parse

        import requests

        from config import Config

        encoded_prompt = urllib.parse.quote(prompt)
        w, h = size.split("x")
        seed = secrets.SystemRandom().randint(1, 1000000)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={w}&height={h}&nologo=true&seed={seed}"

        logger.info(f"Fetching Pollinations image from {url[:80]}...")

        try:
            response = requests.get(url, stream=True, timeout=60)
            response.raise_for_status()
        except requests.exceptions.RequestException as req_err:
            logger.warning(f"Request to Pollinations failed: {req_err}")
            return {"success": False, "error": str(req_err)}

        if response.status_code == 200:
            filename = f"media_{int(time.time()*1000)}.png"
            local_path = os.path.join(Config.UPLOAD_FOLDER, filename)
            with open(local_path, "wb") as f:
                for chunk in response.iter_content(8192):
                    f.write(chunk)

            return {
                "success": True,
                "url": f"/static/uploads/{filename}",
                "prompt": prompt,
                "model": "pollinations",
                "provider": "pollinations",
            }
        else:
            raise RuntimeError(f"Pollinations returned status code {response.status_code}")

    # ── Video Generation ───────────────────────────────────────────────────
    @mirror_to_s3
    def generate_video(
        self,
        caption: str,
        platform: str,
        tone: str | None = None,
        image_path: str | None = None,
        logo_path: str | None = None,
    ) -> dict:
        """Generate an actual MP4 video from caption/story text and optional
        reference image. logo_path: the company's real logo, shown at the end
        of the video (see _apply_video_watermark); None -> no logo."""
        if caption and ("CONTENT GENERATION BLOCKED" in caption or "No Strong Match" in caption):
            return {
                "success": False,
                "type": "video",
                "platform": platform,
                "error": "CONTENT GENERATION BLOCKED. Reason: No Strong Match was identified between this competitor topic and the available projects.",
            }

        if getattr(Config, "USE_MOCK_LLM", False):
            logger.info("USE_MOCK_LLM is enabled. Generating mock video asset...")
            return self._generate_mock_video(platform, caption)

        # Video is generated ONLY through HeyRoute (HEYROUTE_VIDEO_API_KEY,
        # HEYROUTE_VIDEO_MODEL). No other video provider
        # (Gemini / Veo, Bedrock) is tried: when HeyRoute fails, the request
        # fails with HeyRoute's own error.
        if not Config.HEYROUTE_VIDEO_API_KEY:
            return {
                "success": False,
                "type": "video",
                "platform": platform,
                "error": "Video generation needs HEYROUTE_VIDEO_API_KEY in .env (HeyRoute is the only video provider).",
            }

        video_models = [m for m in (Config.HEYROUTE_VIDEO_MODEL, Config.HEYROUTE_VIDEO_FALLBACK_MODEL) if m]
        # grok-video ignores reference images. minimax-h3 uses one when the post
        # has it; only grok-imagine gets a keyframe generated (and billed) for it.
        takes_reference = any(self._heyroute_takes_reference(m) for m in video_models)
        wants_keyframe = any(self._heyroute_video_family(m) == "grok-imagine" for m in video_models)
        resolved_image = self._resolve_image_path(image_path) if takes_reference else None
        keyframe_cost = 0.0  # the keyframe is a billed image: charged with the video
        if wants_keyframe and not resolved_image:
            logger.info("No user image uploaded for video. Auto-generating keyframe image...")
            keyframe_res = self.generate_image(caption, platform, tone)
            if keyframe_res.get("success") and keyframe_res.get("url"):
                resolved_image = self._resolve_image_path(keyframe_res["url"])
                keyframe_cost = float(keyframe_res.get("cost") or 0)
        source_image_url = f"/{resolved_image.replace(os.sep, '/').lstrip('/')}" if resolved_image else None

        prompt = self._build_video_prompt(caption, platform, tone, has_reference_image=bool(resolved_image))
        if any(self._heyroute_makes_audio(m) for m in video_models):
            # The model makes video and sound together: the narration goes in its
            # prompt, short enough to finish before the logo's last two seconds
            clip_seconds = max(4, min(self._MAX_VIDEO_SECONDS, int(Config.HEYROUTE_VIDEO_SECONDS)))
            narration = self._extract_speech_dialogue(caption, max_seconds=max(3, clip_seconds - 2))
            if narration:
                prompt += (
                    f' Audio: a warm, natural human narrator says, in a relaxed conversational voice: "{narration}" '
                    "Soft background music under the voice. No other speech."
                )

        try:
            try:
                result = self._generate_video_heyroute(prompt, platform, image_path=resolved_image)
            except Exception as heyroute_err:
                logger.warning(f"{heyroute_err}")
                return {"success": False, "type": "video", "platform": platform, "error": str(heyroute_err)}

            # --- Single-Pass Native Video + Audio Optimization ---
            if result.get("url") and result.get("has_native_audio"):
                logger.warning(
                    "Single-pass native video+audio generated successfully. Skipping separate TTS audio merging."
                )
                local_name = result["url"].split("/")[-1]
                local_path = os.path.join(self.upload_folder, local_name)
                if os.path.exists(local_path):
                    self._apply_video_watermark(local_path, logo_path)

                return {
                    "success": True,
                    "type": "video",
                    "platform": platform,
                    "url": result["url"],
                    "prompt": prompt,
                    "duration": result.get("duration"),
                    "resolution": self._video_resolution(result["url"]),
                    "model": result.get("model"),
                    "cost": round(float(result.get("cost") or 0) + keyframe_cost, 6),
                    "provider": result.get("provider"),
                    "has_native_audio": True,
                    "audio_mode": "single_pass_native",
                    "source_image_url": source_image_url,
                }

            # --- Post-processing for a clip that came back without audio ---
            if result.get("url"):
                try:
                    # Resolve silent video path
                    silent_video_path = self._resolve_image_path(result["url"])
                    if silent_video_path and os.path.exists(silent_video_path):
                        # Extract dialogue for TTS
                        # The voiceover has to fit the clip: the final video is never
                        # longer than what was generated (15 s at most)
                        clip_seconds = min(self._MAX_VIDEO_SECONDS, float(result.get("duration") or self._MAX_VIDEO_SECONDS))
                        # A silent clip stays silent unless the separate voiceover is switched on
                        tts_text = (
                            self._extract_speech_dialogue(caption, max_seconds=clip_seconds)
                            if Config.VIDEO_TTS_FALLBACK else None
                        )
                        temp_audio_path = None
                        if tts_text:
                            temp_audio_path = self._synthesize_voiceover(tts_text, tone)

                        # Output path
                        processed_video_name = f"processed_{os.path.basename(silent_video_path)}"
                        processed_video_path = os.path.join(self.upload_folder, processed_video_name)

                        from moviepy.audio.io.AudioFileClip import AudioFileClip
                        from moviepy.video.io.VideoFileClip import VideoFileClip

                        with VideoFileClip(silent_video_path) as video_clip:
                            # 1. Crop to the platform's aspect ratio (no upscaling)
                            aspect_w, aspect_h = self._VIDEO_ASPECTS.get(platform, (16, 9))
                            target_aspect = aspect_w / aspect_h
                            orig_w, orig_h = video_clip.w, video_clip.h
                            orig_aspect = orig_w / orig_h

                            if orig_aspect > target_aspect:
                                # Clip is wider than target aspect ratio. Keep height, crop width.
                                crop_h = orig_h
                                crop_w = int(orig_h * target_aspect) // 2 * 2  # libx264 needs even sizes
                                x1 = (orig_w - crop_w) // 2
                                y1 = 0
                                x2 = x1 + crop_w
                                y2 = crop_h
                            else:
                                # Clip is taller than target aspect ratio. Keep width, crop height.
                                crop_w = orig_w
                                crop_h = int(orig_w / target_aspect) // 2 * 2
                                x1 = 0
                                y1 = (orig_h - crop_h) // 2
                                x2 = crop_w
                                y2 = y1 + crop_h

                            # Crop video using moviepy v2 with_effects or moviepy v1 crop function
                            try:
                                from moviepy.video.fx import Crop

                                video_cropped = video_clip.with_effects([Crop(x1=x1, y1=y1, x2=x2, y2=y2)])
                            except ImportError:
                                try:
                                    from moviepy.video.fx.crop import crop

                                    video_cropped = crop(video_clip, x1=x1, y1=y1, x2=x2, y2=y2)
                                except ImportError:
                                    video_cropped = video_clip

                            video_resized = video_cropped

                            # 2. Merge audio if available
                            if temp_audio_path and os.path.exists(temp_audio_path):
                                with AudioFileClip(temp_audio_path) as audio_clip:
                                    # The video keeps its own length (never looped to fit a
                                    # long voiceover, never over 15 s); audio that still
                                    # overruns is cut at the end of the clip
                                    final_seconds = min(video_resized.duration, self._MAX_VIDEO_SECONDS)
                                    if hasattr(video_resized, "with_audio"):
                                        if video_resized.duration > final_seconds:
                                            video_resized = video_resized.subclipped(0, final_seconds)
                                        if audio_clip.duration > final_seconds:
                                            audio_clip = audio_clip.subclipped(0, final_seconds)
                                        final_clip = video_resized.with_audio(audio_clip)
                                    else:
                                        if video_resized.duration > final_seconds:
                                            video_resized = video_resized.subclip(0, final_seconds)
                                        if audio_clip.duration > final_seconds:
                                            audio_clip = audio_clip.subclip(0, final_seconds)
                                        final_clip = video_resized.set_audio(audio_clip)

                                    final_clip.write_videofile(
                                        processed_video_path,
                                        codec="libx264",
                                        audio_codec="aac",
                                        temp_audiofile=os.path.join(
                                            self.upload_folder, f"temp_{uuid.uuid4().hex[:8]}.m4a"
                                        ),
                                        remove_temp=True,
                                        logger=None,
                                    )
                            else:
                                # Just output the cropped/resized silent video
                                video_resized.write_videofile(processed_video_path, codec="libx264", logger=None)

                        # Clean up temporary audio file
                        if temp_audio_path:
                            try:
                                os.remove(temp_audio_path)
                            except Exception:
                                pass

                        # Replace the silent video file with the processed one
                        if os.path.exists(processed_video_path):
                            os.replace(processed_video_path, silent_video_path)
                            logger.info(
                                f"Video cropped to {aspect_w}:{aspect_h} at {silent_video_path}"
                            )
                    # Apply watermark after processing/saving
                    if silent_video_path is not None:
                        self._apply_video_watermark(silent_video_path, logo_path)

                except Exception as merge_err:
                    logger.warning(f"Video post-processing failed: {merge_err}")

            return {
                "success": True,
                "type": "video",
                "platform": platform,
                "url": result["url"],
                "prompt": result["prompt"],
                "duration": result.get("duration"),
                "resolution": self._video_resolution(result["url"]),
                "model": result["model"],
                "cost": round(float(result.get("cost") or 0) + keyframe_cost, 6),
                "provider": result.get("provider") or self.media_provider,
                # None when there is no reference image (none given, or the
                # auto-keyframe failed) - the video is generated without one.
                "source_image_url": source_image_url,
            }
        except Exception as e:
            return {
                "success": False,
                "type": "video",
                "platform": platform,
                "error": str(e),
            }

    # ── Video Storyboard Generation (fallback / reference) ─────────────────
    def generate_video_storyboard(self, caption: str, platform: str, tone: str | None = None) -> dict:
        """
        Generate a detailed video script/storyboard using the LLM.
        Actual video rendering needs Runway ML, Sora, or similar.
        """
        platform_video = {
            "instagram": "Instagram Reel (9:16 vertical, 15–60 seconds)",
            "facebook": "Facebook Video (16:9 landscape or 1:1 square, 30–90 seconds)",
            "linkedin": "LinkedIn Video (16:9 landscape, 30–90 seconds, professional)",
        }.get(platform, "social media video (16:9, 30–60 seconds)")

        tone_hint = f" Tone: {tone}." if tone else ""

        system = f"""You are a professional video content director for {platform.capitalize()}.
Create a detailed video storyboard/script for {platform_video}.{tone_hint}
Return JSON with keys:
- title: catchy video title
- duration: total duration in seconds
- hook: opening hook (first 3 seconds)
- scenes: array of scenes, each with {{ scene_number, duration, visual_description, audio_narration, on_screen_text, transition }}
- cta: call to action
- music_mood: suggested background music style
- production_notes: tips for filming
"""
        user = f"Caption/Brief: {caption}\nPlatform: {platform.capitalize()}"

        try:
            storyboard = self.llm_service.generate_json(system, user, temperature=0.7, max_tokens=1500)
            return {
                "success": True,
                "type": "video_storyboard",
                "platform": platform,
                "storyboard": storyboard,
            }
        except Exception as e:
            return {
                "success": False,
                "type": "video_storyboard",
                "platform": platform,
                "error": str(e),
            }

    def _apply_video_watermark(self, video_path: str, logo_path: str | None) -> None:
        """Overlays the company's real logo (brand_logo_service.resolve_logo_path)
        at the end of the video. No logo -> no branding, rather than stamping
        another company's (this used to always use StradIT's Logo.png)."""
        try:
            if not logo_path or not os.path.exists(logo_path):
                logger.warning("No brand logo for this user, skipping video watermark.")
                return

            try:
                # MoviePy v1.x imports
                from moviepy.editor import CompositeVideoClip, ImageClip, VideoFileClip
            except ImportError:
                # MoviePy v2.x fallback
                from moviepy.video.compositing.CompositeVideoClip import (
                    CompositeVideoClip,
                )
                from moviepy.video.io.VideoFileClip import VideoFileClip
                from moviepy.video.VideoClip import ImageClip

            with VideoFileClip(video_path) as video:
                duration = video.duration
                start_time = max(0, duration - 2.0)

                logo_clip = ImageClip(logo_path)

                target_logo_width = int(video.w * 0.3)
                aspect = logo_clip.h / logo_clip.w
                target_logo_height = int(target_logo_width * aspect)

                if hasattr(logo_clip, "resized"):
                    logo_clip = logo_clip.resized((target_logo_width, target_logo_height))
                elif hasattr(logo_clip, "resize"):
                    logo_clip = logo_clip.resize((target_logo_width, target_logo_height))
                else:
                    from moviepy.video.fx.resize import resize

                    logo_clip = resize(logo_clip, (target_logo_width, target_logo_height))

                pos_x = (video.w - target_logo_width) // 2
                pos_y = (video.h - target_logo_height) // 2

                if hasattr(logo_clip, "with_start"):
                    # Moviepy v2
                    logo_clip = (
                        logo_clip.with_start(start_time)
                        .with_duration(duration - start_time)
                        .with_position((pos_x, pos_y))
                    )
                    try:
                        from moviepy.video.fx import CrossFadeIn

                        logo_clip = logo_clip.with_effects([CrossFadeIn(0.5)])
                    except ImportError:
                        pass
                else:
                    # Moviepy v1
                    logo_clip = (
                        logo_clip.set_start(start_time)
                        .set_duration(duration - start_time)
                        .set_position((pos_x, pos_y))
                        .crossfadein(0.5)
                    )

                final_video = CompositeVideoClip([video, logo_clip])

                temp_path = video_path.replace(".mp4", "_wm.mp4")
                final_video.write_videofile(temp_path, codec="libx264", audio_codec="aac", logger=None)

            os.replace(temp_path, video_path)
            logger.info(f"Successfully watermarked video: {video_path}")
        except Exception as e:
            logger.warning(f"Failed to watermark video: {e}")

    def _clean_text_for_tts(self, text: str) -> str:
        if not text:
            return ""
        # Remove hashtags
        words = [w for w in text.split() if not w.startswith("#")]
        cleaned = " ".join(words)
        import re

        cleaned = re.sub(r"https?://\S+", "", cleaned)  # Remove URLs
        cleaned = re.sub(r"\[.*?\]", "", cleaned)  # Remove bracketed placeholders
        # Clean up double spaces
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip()

    def _extract_headline(self, caption: str) -> str:
        """Derives a short, complete, punchy headline (under 8 words) from a
        longer caption for on-image text overlays. A dedicated LLM call
        rather than blind character truncation, which can chop a phrase off
        mid-word (e.g. "AI-driven due diligence" -> "AI-dri")."""
        if not caption:
            return ""
        try:
            headline = self.llm_service.generate(
                system_prompt=(
                    "Extract a short, punchy, grammatically complete headline (strictly under 8 words) "
                    "that captures the core message of the given social media caption. "
                    "Output ONLY the headline text, nothing else - no quotes, no trailing punctuation."
                ),
                user_prompt=caption[:500],
                temperature=0.5,
                max_tokens=300,
            )
            return headline.strip().strip('"').strip("'")[:80]
        except Exception as e:
            logger.warning(f"Headline extraction failed: {e}. Using fallback.")
            trimmed = caption.split(".")[0].split("\n")[0].strip()[:60]
            return trimmed.rsplit(" ", 1)[0] if " " in trimmed else trimmed

    def _enhance_image_prompt(self, user_caption: str, platform: str, tone: str | None = None) -> str:
        """
        Enhance a simple user prompt into a professional, visually rich prompt
        for image generation models, optimized for high aesthetic quality.
        """
        platform_style = {
            "instagram": "modern lifestyle, aesthetic, high engagement, rich colors",
            "facebook": "bright, friendly, community-oriented, warm lighting",
            "linkedin": "professional corporate, sleek modern office, clean layout, corporate executive",
        }.get(platform, "modern and professional")

        tone_hint = f" with a {tone} tone" if tone else ""

        system_prompt = (
            "You are an expert AI image prompt engineer. Your job is to transform a simple social media image request "
            "into a highly detailed, visually rich, and professional prompt for image generation models (like Google Nano Banana / Amazon Nova Canvas). "
            "Describe the scene in vivid detail: the main subject, clothing, environment/background, lighting (e.g. volumetric, warm golden hour, professional studio lighting), "
            "composition (e.g. medium shot, rule of thirds), camera details (e.g. shot on 35mm lens, shallow depth of field, sharp focus), and color palette. "
            "Keep the style realistic and photorealistic unless requested otherwise. "
            "TEXT OVERLAY: Extract a short, punchy headline (under 8 words) that captures the core message of the request. "
            "Explicitly instruct the image to render that exact headline as bold, clearly legible text integrated into the "
            "composition (large clean sans-serif typography, high contrast against the background, positioned so it doesn't "
            "cover the main subject's face). Do not add any other text, captions, or watermarks beyond that one headline. "
            "BRANDING: the prompt must explicitly state that no logo, wordmark or company name is drawn anywhere in the "
            "image (the real logo is added afterwards) and that the bottom-right corner stays free of important content. "
            "SOURCE OF TRUTH ENFORCEMENT: The visual prompt must exactly represent the project and problem context given in the request. Do NOT invent or hallucinate features, projects, or problems. "
            "Output ONLY the final enhanced prompt in a single paragraph, under 600 characters."
        )

        user_prompt = f"Request: {user_caption}\nPlatform: {platform} ({platform_style}){tone_hint}"

        try:
            # 200 tokens was too tight for reasoning models, which spend part of
            # the budget on internal chain-of-thought before the actual answer -
            # under-budgeting risks getting cut off mid-thought instead of the
            # finished prompt. The final prompt itself is still capped at 600 chars.
            enhanced = self.llm_service.generate(
                system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.7, max_tokens=700
            )
            return enhanced.strip()[:600]
        except Exception as e:
            logger.warning(f"Image prompt enhancement failed: {e}. Using fallback.")
            # No further LLM call here - the one above just failed. Trim to the
            # last full word within the limit instead of a blind character cut,
            # which can chop a phrase off mid-word (e.g. "AI-driven" -> "AI-dri").
            trimmed = user_caption.split(".")[0].split("\n")[0].strip()[:60]
            headline = trimmed.rsplit(" ", 1)[0] if " " in trimmed else trimmed
            return (
                f"A professional, photorealistic social media image for {platform.capitalize()}: {user_caption}. "
                f'Render the bold headline text "{headline}" in large clean sans-serif typography, high contrast, '
                f"not covering the main subject's face. {NO_AI_LOGO_RULE} "
                f"Sleek visual composition, shallow depth of field, studio lighting, highly detailed."
            )

    def _synthesize_voiceover(self, text: str, tone: str) -> str:
        """Speak `text` into a temp audio file in the upload folder and return
        its path: a natural Gemini voice when GOOGLE_API_KEY is set, else (or
        if that call fails) the robotic gTTS voice."""
        base = os.path.join(self.upload_folder, f"temp_tts_{uuid.uuid4().hex[:8]}")
        google_key = getattr(Config, "GOOGLE_API_KEY", None) or os.getenv("GOOGLE_API_KEY")
        if google_key:
            try:
                import re
                import wave

                import google.genai as genai
                from google.genai import types

                style = f"a {tone.lower()}" if tone and tone.lower() not in ("auto", "auto-detect") else "a warm"
                # 60 s cap: without it a stalled connection holds the whole video back
                client = genai.Client(api_key=google_key, http_options=types.HttpOptions(timeout=60_000))
                response = client.models.generate_content(
                    model=Config.VOICEOVER_TTS_MODEL,
                    contents=f"Read this aloud as {style}, natural human narrator of a brand video, "
                             f"at a relaxed conversational pace: {text}",
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=Config.VOICEOVER_VOICE)
                            )
                        ),
                    ),
                )
                audio = response.candidates[0].content.parts[0].inline_data
                # Raw 16-bit mono PCM, e.g. "audio/L16;codec=pcm;rate=24000"
                rate = re.search(r"rate=(\d+)", audio.mime_type or "")
                with wave.open(base + ".wav", "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(int(rate.group(1)) if rate else 24000)
                    wav.writeframes(audio.data)
                return base + ".wav"
            except Exception as tts_err:  # noqa: BLE001 - a voiceover is better robotic than missing
                logger.warning(f"Gemini voiceover failed, using gTTS: {tts_err}")

        from gtts import gTTS

        tld_map = {
            "b2b tech leader": "co.uk",
            "bold viral marketer": "com",
            "friendly lifestyle coach": "com.au",
            "high-growth startup": "co.in",
            "standard enterprise": "ca",
            "professional": "co.uk",
            "casual": "com.au",
            "enthusiastic": "com",
            "urgent": "com",
        }
        gTTS(text=text, lang="en", tld=tld_map.get((tone or "").lower(), "com")).save(base + ".mp3")
        return base + ".mp3"

    _MAX_VIDEO_SECONDS = 15  # no finished video is longer than this
    _VOICEOVER_WORDS_PER_SECOND = 2.2  # relaxed narration pace

    @staticmethod
    def _fit_words(text: str, max_words: int) -> str:
        """`text` cut to `max_words`, at the last sentence end when there is one."""
        import re

        words = text.split()
        if len(words) <= max_words:
            return text
        cut = " ".join(words[:max_words])
        ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", cut)]
        return cut[: ends[-1]] if ends else cut

    def _extract_speech_dialogue(self, caption: str, max_seconds: float | None = None) -> str:
        """
        Extract only the spoken dialogue or text that should be read aloud from a prompt.
        If no dialogue is specified, return the cleaned caption. With max_seconds, the
        result is short enough to be spoken in that time.
        """
        import re

        max_words = max(6, int(max_seconds * self._VOICEOVER_WORDS_PER_SECOND)) if max_seconds else None

        # Check for explicit 'Speak exactly this dialogue only: "..."' or similar pattern
        match = re.search(
            r'Speak\s+exactly\s+this\s+dialogue\s+only[:\s]*["“](.*?)["”]', caption, re.IGNORECASE | re.DOTALL
        )
        if match:
            extracted = match.group(1).strip().lstrip("—").strip()
            if extracted and (max_words is None or len(extracted.split()) <= max_words):
                return extracted

        system_prompt = (
            "You are an AI assistant. Extract ONLY the spoken dialogue, voiceover script, or text that should be "
            "spoken aloud from the user's prompt. Do not include any instructions, scene descriptions, metadata, "
            "or negative constraints. Return ONLY the exact dialogue text to be read by a text-to-speech reader. "
            'If the prompt contains dialogue quotes (e.g. Speak exactly this dialogue only: "..."), return '
            "only the content inside those quotes. If there is no specific dialogue, return a cleaned version "
            "of the prompt suitable for speaking."
        )
        if max_words:
            system_prompt += (
                f" The voiceover must be spoken in under {int(max_seconds)} seconds: write at most {max_words} "
                "words, as one or two complete sentences that carry the main message. Never exceed the word limit."
            )
        try:
            dialogue = self.llm_service.generate(
                system_prompt=system_prompt, user_prompt=caption, temperature=0.1, max_tokens=300
            )
            clean_dialogue = dialogue.strip().replace('"', "").lstrip("—").strip() or self._clean_text_for_tts(caption)
        except Exception as e:
            logger.warning(f"Dialogue extraction failed: {e}")
            clean_dialogue = self._clean_text_for_tts(caption)
        return self._fit_words(clean_dialogue, max_words) if max_words else clean_dialogue
