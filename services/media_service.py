"""
Media generation service.
- Image: DALL-E 3 via OpenRouter/OpenAI
- Video: Storyboard/script generation (AI video tools like Runway/Sora need separate API)
"""

import openai
import requests
import os
import uuid
from config import Config


class MediaGenerationService:
    """Generates social media images via DALL-E 3 and video storyboards via LLM."""

    def __init__(self):
        api_key = Config.OPENAI_API_KEY
        self.uses_openrouter = bool(
            api_key and (api_key.startswith("sk-or-") or "openrouter" in api_key.lower())
        )
        if self.uses_openrouter:
            self.client = openai.OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1"
            )
        else:
            self.client = openai.OpenAI(api_key=api_key)

        self.upload_folder = Config.UPLOAD_FOLDER
        os.makedirs(self.upload_folder, exist_ok=True)

    # ── Image Generation ───────────────────────────────────────────────────
    def generate_image(self, caption: str, platform: str, tone: str = None) -> dict:
        """
        Generate a social media image using DALL-E 3.
        Returns: { url, local_path, prompt, size, platform }
        """
        size_map = {
            "instagram": "1024x1024",
            "facebook":  "1792x1024",
            "linkedin":  "1792x1024",
        }
        size = size_map.get(platform, "1024x1024")

        platform_style = {
            "instagram": "vibrant, high-contrast, aesthetically pleasing, Instagram-worthy",
            "facebook":  "engaging, community-oriented, warm and inviting, Facebook post style",
            "linkedin":  "professional, clean, corporate, business-appropriate, LinkedIn post style",
        }.get(platform, "professional and engaging")

        tone_hint = f", {tone} tone" if tone else ""

        prompt = (
            f"A high-quality social media image for {platform.capitalize()}: "
            f"{caption[:200]}. "
            f"Style: {platform_style}{tone_hint}. "
            f"No text overlays, photorealistic, suitable for social media marketing."
        )

        try:
            # OpenRouter uses auto/low/medium/high; native OpenAI uses standard/hd.
            image_kwargs = {
                "model": "openai/dall-e-3" if self.uses_openrouter else "dall-e-3",
                "prompt": prompt,
                "size": size,
                "quality": "medium" if self.uses_openrouter else "standard",
                "n": 1,
            }
            response = self.client.images.generate(**image_kwargs)
            image_url = response.data[0].url
            revised_prompt = getattr(response.data[0], "revised_prompt", prompt)

            # Download and save locally
            local_filename = f"gen_{platform}_{uuid.uuid4().hex[:8]}.png"
            local_path = os.path.join(self.upload_folder, local_filename)
            img_data = requests.get(image_url, timeout=30).content
            with open(local_path, "wb") as f:
                f.write(img_data)

            return {
                "success": True,
                "type": "image",
                "platform": platform,
                "url": f"/static/uploads/{local_filename}",
                "original_url": image_url,
                "prompt": revised_prompt,
                "size": size,
            }

        except Exception as e:
            return {
                "success": False,
                "type": "image",
                "platform": platform,
                "error": str(e),
            }

    # ── Video Storyboard Generation ────────────────────────────────────────
    def generate_video_storyboard(self, caption: str, platform: str, tone: str = None) -> dict:
        """
        Generate a detailed video script/storyboard using the LLM.
        Actual video rendering needs Runway ML, Sora, or similar.
        """
        platform_video = {
            "instagram": "Instagram Reel (9:16 vertical, 15–60 seconds)",
            "facebook":  "Facebook Video (16:9 landscape or 1:1 square, 30–90 seconds)",
            "linkedin":  "LinkedIn Video (16:9 landscape, 30–90 seconds, professional)",
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
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.7,
                max_tokens=1500,
                response_format={"type": "json_object"},
            )
            import json
            storyboard = json.loads(response.choices[0].message.content)
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
