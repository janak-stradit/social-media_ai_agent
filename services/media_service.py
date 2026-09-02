"""
Media generation service.
- Image: Z.AI GLM-Image, OpenRouter Image API, or DALL-E 3 (OpenAI)
- Video: Z.AI CogVideoX (image-to-video), OpenRouter Video API
"""

import openai
import requests
import os
import uuid
import base64
import time
import mimetypes
from config import Config
from services.llm_service import LLMService


class MediaGenerationService:
    """Generates social media images and videos via Z.AI, OpenRouter, or OpenAI."""

    OPENROUTER_BASE = "https://openrouter.ai/api/v1"

    def __init__(self):
        self.llm_service = LLMService()
        api_key = Config.OPENAI_API_KEY
        self.api_key = api_key
        self.zai_api_key = Config.Z_AI_API_KEY
        
        # Force Bedrock provider strictly as requested by the user
        self.media_provider = 'bedrock'
        self.uses_bedrock = True
        self.uses_zai = False
        self.uses_openrouter = False

        self.zai_client = None
        if self.zai_api_key:
            self.zai_client = openai.OpenAI(
                api_key=self.zai_api_key,
                base_url=Config.Z_AI_BASE_URL,
            )

        if self.uses_openrouter:
            self.client = openai.OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1"
            )
        elif self.uses_zai:
            self.client = self.zai_client
        else:
            self.client = openai.OpenAI(api_key=api_key)

        # Initialize AWS Bedrock and S3 Clients if Bedrock is selected
        self.bedrock_client = None
        self.s3_client = None
        if self.uses_bedrock:
            try:
                import boto3
                from botocore.config import Config as BotoConfig
                
                aws_access_key = getattr(Config, 'AWS_ACCESS_KEY_ID', None)
                aws_secret_key = getattr(Config, 'AWS_SECRET_ACCESS_KEY', None)
                aws_profile = getattr(Config, 'AWS_PROFILE', None)
                aws_region = getattr(Config, 'AWS_REGION', 'us-east-1')
                
                session_kwargs = {}
                if aws_profile:
                    session_kwargs['profile_name'] = aws_profile
                elif aws_access_key and aws_secret_key:
                    session_kwargs['aws_access_key_id'] = aws_access_key
                    session_kwargs['aws_secret_access_key'] = aws_secret_key
                
                if aws_region:
                    session_kwargs['region_name'] = aws_region
                
                session = boto3.Session(**session_kwargs)
                
                boto_config = BotoConfig(
                    read_timeout=300,
                    connect_timeout=60,
                    retries={"max_attempts": 3}
                )
                
                self.bedrock_client = session.client(
                    "bedrock-runtime",
                    config=boto_config
                )
                self.s3_client = session.client(
                    "s3"
                )
            except Exception as e:
                print(f"[MediaGenerationService] Bedrock client initialization failed: {e}")

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
            payload = {
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
        return None

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
        cleaned = re.sub(r'Speak\s+exactly\s+this\s+dialogue\s+only[:\s]*["“].*?["”]', '', text, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r'Do\s+not\s+(?:say|repeat).*?["“].*?["”]', '', cleaned, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r'["“].*?["”]', '', cleaned)  # remove quoted text
        cleaned = re.sub(r'#\w+', '', cleaned)        # remove hashtags
        cleaned = re.sub(r'https?://\S+', '', cleaned) # remove URLs
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned

    def _build_video_prompt(self, caption: str, platform: str, tone: str = None, has_reference_image: bool = False) -> str:
        platform_style = {
            "instagram": "vibrant portrait vertical video, cinematic lighting, modern style",
            "facebook": "professional, warm corporate, polished corporate video",
            "linkedin": "premium executive, corporate office, clean aesthetic, professional",
        }.get(platform, "highly professional and cinematic")

        lower_caption = caption.lower()
        is_character_continuation = any(k in lower_caption for k in [
            'character', 'preserve', 'attached', 'aidan', 'continuation', 'part 1', 'part 2', 'same face'
        ])

        if has_reference_image or is_character_continuation:
            prompt = (
                "Animate source image character: professional executive speaking confidently to camera "
                "with realistic speaking lip sync, subtle hand gestures, organic head nods, and direct eye contact. "
                "Steady medium shot with slow cinematic push-in. Preserve exact face, hairstyle, skin tone, outfit, logo, lighting, "
                "and modern office background. 4k photorealistic video, 24fps, smooth natural motion."
            )
        else:
            cleaned_desc = self._clean_motion_text(caption)
            if len(cleaned_desc) > 200:
                cleaned_desc = self._compress_video_prompt(cleaned_desc)
            if not cleaned_desc:
                cleaned_desc = "Professional presenter speaking directly to camera in a modern corporate studio"

            prompt = (
                f"High-quality cinematic video: {cleaned_desc}. "
                f"Style: {platform_style}. "
                f"Realistic character motion, natural facial expressions, continuous speaking lip-sync, "
                f"subtle gestures, professional studio lighting, 4k ultra-high-fidelity render."
            )

        # Ensure strict adherence to model limits (Amazon Nova Reel 512-character max)
        return prompt[:480]

    def _generate_video_openrouter(self, prompt: str, platform: str, image_path: str = None) -> dict:
        """Submit an OpenRouter video job, poll until done, and save the MP4 locally."""
        aspect_ratio_map = {
            "instagram": "9:16",
            "facebook": "16:9",
            "linkedin": "16:9",
        }
        payload = {
            "model": Config.VIDEO_MODEL,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio_map.get(platform, "16:9"),
            "duration": Config.VIDEO_DURATION,
            "resolution": Config.VIDEO_RESOLUTION,
            "generate_audio": True,
        }

        resolved_image = self._resolve_image_path(image_path)
        if resolved_image:
            payload["frame_images"] = [{
                "type": "image_url",
                "image_url": {"url": self._image_to_data_uri(resolved_image)},
                "frame_type": "first_frame",
            }]

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

    def _save_image_bytes(self, img_data: bytes, platform: str) -> tuple[str, str]:
        local_filename = f"gen_{platform}_{uuid.uuid4().hex[:8]}.png"
        local_path = os.path.join(self.upload_folder, local_filename)
        with open(local_path, "wb") as f:
            f.write(img_data)
        return local_filename, local_path

    def _generate_image_openrouter(self, prompt: str, platform: str, size: str, image_path: str = None) -> dict:
        """Generate via OpenRouter's dedicated /v1/images API."""
        aspect_ratio_map = {
            "instagram": "1:1",
            "facebook": "16:9",
            "linkedin": "16:9",
        }
        payload = {
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
            payload["input_references"] = [{
                "type": "image_url",
                "image_url": {"url": self._image_to_data_uri(resolved_image)},
            }]

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

    # ── Image Generation ───────────────────────────────────────────────────
    def _parse_size(self, size_str: str) -> tuple[int, int]:
        try:
            w_str, h_str = size_str.split('x')
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

    def _get_image_as_jpeg_base64(self, image_path: str, target_size: tuple[int, int] = None) -> str:
        from PIL import Image
        import io
        resolved = self._resolve_image_path(image_path)
        if not resolved:
            raise RuntimeError(f"Reference image not found: {image_path}")
            
        with Image.open(resolved) as img:
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')
            if target_size:
                resample = getattr(Image, 'Resampling', None)
                resample_method = resample.LANCZOS if resample else getattr(Image, 'ANTIALIAS', 3)
                img = img.resize(target_size, resample_method)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=90)
            return base64.b64encode(buffer.getvalue()).decode('utf-8')

    def _generate_image_bedrock(self, prompt: str, platform: str, size: str, image_path: str = None) -> dict:
        """Generate an image using AWS Bedrock (low cost model, e.g. Amazon Nova Canvas)."""
        if not self.bedrock_client:
            raise RuntimeError("AWS Bedrock client is not initialized. Check AWS credentials.")
            
        model_id = getattr(Config, 'BEDROCK_IMAGE_MODEL', 'amazon.nova-canvas-v1:0')
        width, height = self._parse_size(size)
        import random
        import json
        seed = random.randint(0, 2147483646)
        
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
                        "similarityStrength": 0.7
                    },
                    "imageGenerationConfig": {
                        "numberOfImages": 1,
                        "quality": "standard",
                        "height": height,
                        "width": width,
                        "seed": seed
                    }
                }
                response = self.bedrock_client.invoke_model(
                    body=json.dumps(payload),
                    modelId=model_id,
                    accept="application/json",
                    contentType="application/json"
                )
            except Exception as variation_err:
                print(f"[Media Service] Bedrock IMAGE_VARIATION payload notice: {variation_err}. Falling back to TEXT_IMAGE taskType...")
                payload = {
                    "taskType": "TEXT_IMAGE",
                    "textToImageParams": {
                        "text": prompt_text
                    },
                    "imageGenerationConfig": {
                        "numberOfImages": 1,
                        "quality": "standard",
                        "height": height,
                        "width": width,
                        "seed": seed
                    }
                }
                response = self.bedrock_client.invoke_model(
                    body=json.dumps(payload),
                    modelId=model_id,
                    accept="application/json",
                    contentType="application/json"
                )
        else:
            payload = {
                "taskType": "TEXT_IMAGE",
                "textToImageParams": {
                    "text": prompt_text
                },
                "imageGenerationConfig": {
                    "numberOfImages": 1,
                    "quality": "standard",
                    "height": height,
                    "width": width,
                    "seed": seed
                }
            }
            response = self.bedrock_client.invoke_model(
                body=json.dumps(payload),
                modelId=model_id,
                accept="application/json",
                contentType="application/json"
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

    def _generate_video_bedrock(self, prompt: str, platform: str, image_path: str = None) -> dict:
        """Generate a video using AWS Bedrock (low cost model, e.g. Amazon Nova Reel)."""
        if not self.bedrock_client or not self.s3_client:
            raise RuntimeError("AWS Bedrock or S3 client is not initialized. Check AWS credentials.")
            
        # Amazon Nova Reel prompts must be strictly <= 512 characters
        prompt = prompt[:512]
            
        model_id = getattr(Config, 'BEDROCK_VIDEO_MODEL', 'amazon.nova-reel-v1:0')
        s3_bucket = getattr(Config, 'AWS_S3_BUCKET', None)
        if not s3_bucket:
            raise RuntimeError("AWS_S3_BUCKET is not configured in environment variables. Bedrock video generation requires S3.")
            
        import random
        seed = random.randint(0, 2147483646)
        resolved_image = self._resolve_image_path(image_path)
        
        dimension = "1280x720"
        if resolved_image:
            input_image_b64 = self._get_image_as_jpeg_base64(resolved_image, target_size=(1280, 720))
            model_input = {
                "taskType": "TEXT_VIDEO",
                "textToVideoParams": {
                    "text": prompt,
                    "images": [
                        {
                            "format": "jpeg",
                            "source": {
                                                    "bytes": input_image_b64
                            }
                        }
                    ]
                },
                "videoGenerationConfig": {
                    "fps": 24,
                    "durationSeconds": 6,
                    "dimension": dimension,
                    "seed": seed
                }
            }
        else:
            model_input = {
                "taskType": "TEXT_VIDEO",
                "textToVideoParams": {
                    "text": prompt
                },
                "videoGenerationConfig": {
                    "fps": 24,
                    "durationSeconds": 6,
                    "dimension": dimension,
                    "seed": seed
                }
            }
            
        job_id = uuid.uuid4().hex
        s3_uri = f"s3://{s3_bucket.strip('/')}/bedrock-video-outputs/{job_id}/"
        
        output_config = {
            "s3OutputDataConfig": {
                "s3Uri": s3_uri
            }
        }
        if getattr(Config, 'AWS_BUCKET_OWNER', None):
            output_config["s3OutputDataConfig"]["bucketOwner"] = Config.AWS_BUCKET_OWNER
            
        response = self.bedrock_client.start_async_invoke(
            clientRequestToken=str(uuid.uuid4()),
            modelId=model_id,
            modelInput=model_input,
            outputDataConfig=output_config
        )
        
        invocation_arn = response["invocationArn"]
        
        # Poll for completion
        deadline = time.time() + Config.VIDEO_POLL_TIMEOUT
        completed = False
        final_s3_uri = None
        
        while time.time() < deadline:
            poll_resp = self.bedrock_client.get_async_invoke(invocationArn=invocation_arn)
            status = poll_resp.get("status")
            if status == "Completed":
                completed = True
                final_s3_uri = poll_resp['outputDataConfig']['s3OutputDataConfig']['s3Uri']
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
        prefix = parsed.path.lstrip('/')
        
        # List objects under prefix to find the mp4 file
        list_resp = self.s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        contents = list_resp.get('Contents', [])
        
        video_key = None
        for item in contents:
            key = item['Key']
            if key.endswith('.mp4'):
                video_key = key
                break
                
        if not video_key:
            video_key = f"{prefix.rstrip('/')}/output.mp4"
            
        obj_resp = self.s3_client.get_object(Bucket=bucket, Key=video_key)
        video_data = obj_resp['Body'].read()
        
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
        from PIL import Image, ImageDraw
        import uuid

        filename = f"mock_{media_type}_{uuid.uuid4().hex[:8]}.png"
        filepath = os.path.join(self.upload_folder, filename)

        w, h = (1024, 1024) if media_type == 'image' else (1280, 720)
        img = Image.new('RGB', (w, h), color=(30, 41, 59))
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
            
        response = self.client.images.generate(
            model="dall-e-3",
            prompt=prompt[:4000],
            size=oai_size,
            quality="standard",
            n=1,
        )
        image_url = response.data[0].url
        img_data = requests.get(image_url, timeout=30).content
        local_filename, _ = self._save_image_bytes(img_data, platform)
        return {
            "url": f"/static/uploads/{local_filename}",
            "original_url": image_url,
            "prompt": prompt,
            "cost": 0.040,
            "model": "dall-e-3"
        }

    # ── Image Generation ───────────────────────────────────────────────────
    def generate_image(self, caption: str, platform: str, tone: str = None, image_path: str = None) -> dict:
        """
        Generate a social media image.
        Returns: { url, local_path, prompt, size, platform }
        """
        if caption and ("CONTENT GENERATION BLOCKED" in caption or "No Strong Match" in caption):
            return {
                "success": False,
                "type": "image",
                "platform": platform,
                "error": "CONTENT GENERATION BLOCKED. Reason: No Strong Match was identified between this competitor topic and the available projects."
            }

        if getattr(Config, 'USE_MOCK_LLM', False):
            print("[Media Service] USE_MOCK_LLM is enabled. Generating mock image asset...")
            return self._generate_mock_media(platform, "image", caption)

        size_map = {
            "instagram": "1024x1024",
            "facebook":  "1792x1024",
            "linkedin":  "1792x1024",
        }
        size = size_map.get(platform, "1024x1024")

        has_reference = bool(self._resolve_image_path(image_path))

        if has_reference:
            platform_style = {
                "instagram": "vibrant, modern style, portrait orientation, highly polished",
                "facebook":  "warm and inviting, polished and clean look, corporate sharing",
                "linkedin":  "corporate executive, clean design, high-end business style",
            }.get(platform, "professional and engaging")
            tone_hint = f", {tone} tone" if tone else ""
            prompt = (
                f"Create a professional social media image for {platform.capitalize()} based on the uploaded reference image. "
                f"Preserve the main subject's exact facial features, hair, skin tone, and visual identity from the reference image. "
                f"Brief: {caption[:200]}. "
                f"Style: {platform_style}{tone_hint}. "
                f"No text overlays, premium quality, highly detailed."
            )
        else:
            # If the user provides a detailed prompt (like a Midjourney prompt), use it directly
            if len(caption) > 150 or "midjourney" in caption.lower() or "prompt" in caption.lower():
                prompt = caption
            else:
                prompt = self._enhance_image_prompt(caption, platform, tone)

        try:
            if not self.bedrock_client:
                raise RuntimeError("AWS Bedrock client is not initialized")
            result = self._generate_image_bedrock(prompt, platform, size, image_path=image_path)
        except Exception as bedrock_err:
            print(f"[Media Service] Bedrock image generation failed: {bedrock_err}. Falling back to OpenAI DALL-E 3...")
            try:
                result = self._generate_image_openai(prompt, platform, size)
            except Exception as openai_err:
                print(f"[Media Service] OpenAI image generation failed: {openai_err}. Falling back to Pollinations.ai...")
                import urllib.parse
                encoded_prompt = urllib.parse.quote(prompt[:800])
                width, height = size.split('x')
                url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&nologo=true"
                response = requests.get(url, timeout=60)
                if response.status_code != 200:
                    raise Exception(f"Pollinations API returned status {response.status_code}: {response.text[:100]}")
                img_data = response.content
                local_filename, _ = self._save_image_bytes(img_data, platform)
                result = {
                    "url": f"/static/uploads/{local_filename}",
                    "original_url": url,
                    "prompt": prompt,
                    "cost": 0.0,
                    "model": "pollinations",
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
            }

        except Exception as e:
            return {
                "success": False,
                "type": "image",
                "platform": platform,
                "error": str(e),
            }

    def _generate_google_gemini_video(self, prompt: str, platform: str, image_path: str = None) -> dict:
        """Generate a video using Google Gemini / Veo Video Generation API via google-genai SDK."""
        google_key = getattr(Config, 'GOOGLE_API_KEY', None) or os.getenv("GOOGLE_API_KEY")
        if not google_key:
            raise RuntimeError("GOOGLE_API_KEY is missing in your environment or config file.")

        try:
            import google.genai as genai
            from google.genai import types
        except ImportError:
            raise RuntimeError("The 'google-genai' package is required. Run 'pip install google-genai'.")

        model_name = getattr(Config, 'GEMINI_VIDEO_MODEL', 'veo-3.1-generate-preview')
        print(f"[Media Service] Initiating Google Gemini Video generation with model: {model_name}...")

        client = genai.Client(api_key=google_key)
        aspect_ratio = "9:16" if platform == "instagram" else "16:9"

        gen_kwargs = {
            "model": model_name,
            "prompt": prompt[:512],
            "config": types.GenerateVideosConfig(
                aspect_ratio=aspect_ratio,
                duration_seconds=5,
                number_of_videos=1,
                generate_audio=getattr(Config, 'GENERATE_NATIVE_AUDIO', True),
            )
        }

        resolved_image = self._resolve_image_path(image_path)
        if resolved_image and os.path.exists(resolved_image):
            try:
                with open(resolved_image, "rb") as f:
                    img_bytes = f.read()
                gen_kwargs["image"] = types.Image(image_bytes=img_bytes, mime_type="image/jpeg")
            except Exception as img_err:
                print(f"[Media Service] Warning loading image for Gemini Video: {img_err}")

        operation = client.models.generate_videos(**gen_kwargs)

        print("[Media Service] Polling Google Gemini Video operation (Native Single-Pass Video + Audio)...")
        deadline = time.time() + 300
        while not operation.done and time.time() < deadline:
            time.sleep(8)
            operation = client.operations.get(operation)

        if not operation.done:
            raise RuntimeError("Google Gemini Video generation operation timed out after 300s.")

        result = operation.result
        if not result or not getattr(result, 'generated_videos', None):
            raise RuntimeError("Google Gemini Video generation returned empty result.")

        generated_video = result.generated_videos[0]
        filename = f"gemini_video_{uuid.uuid4().hex[:8]}.mp4"
        filepath = os.path.join(self.upload_folder, filename)

        client.files.download(file=generated_video.video, destination=filepath)
        return {
            "success": True,
            "url": f"/static/uploads/{filename}",
            "prompt": prompt,
            "model": model_name,
            "provider": "Google Gemini (Veo)",
            "has_native_audio": getattr(Config, 'GENERATE_NATIVE_AUDIO', True),
            "audio_mode": "single_pass_native"
        }

    # ── Video Generation ───────────────────────────────────────────────────
    def generate_video(self, caption: str, platform: str, tone: str = None, image_path: str = None) -> dict:
        """Generate an actual MP4 video from caption/story text and optional reference image."""
        if caption and ("CONTENT GENERATION BLOCKED" in caption or "No Strong Match" in caption):
            return {
                "success": False,
                "type": "video",
                "platform": platform,
                "error": "CONTENT GENERATION BLOCKED. Reason: No Strong Match was identified between this competitor topic and the available projects."
            }

        if getattr(Config, 'USE_MOCK_LLM', False):
            print("[Media Service] USE_MOCK_LLM is enabled. Generating mock video asset...")
            return self._generate_mock_media(platform, "video", caption)

        resolved_image = self._resolve_image_path(image_path)
        
        # Auto-generate a visual keyframe image if no reference image was provided
        if not resolved_image:
            print("[Media Service] No user image uploaded for video. Auto-generating keyframe image...")
            keyframe_res = self.generate_image(caption, platform, tone)
            if keyframe_res.get("success") and keyframe_res.get("url"):
                resolved_image = self._resolve_image_path(keyframe_res["url"])

        prompt = self._build_video_prompt(
            caption, platform, tone, has_reference_image=bool(resolved_image)
        )

        try:
            try:
                # Check if Gemini / Google API key is set or media provider is gemini
                if (Config.MEDIA_PROVIDER == 'gemini' or os.getenv('MEDIA_PROVIDER') == 'gemini' or Config.GOOGLE_API_KEY or os.getenv('GOOGLE_API_KEY')):
                    print("[Media Service] Attempting video generation via Google Gemini / Veo...")
                    result = self._generate_google_gemini_video(prompt, platform, image_path=resolved_image)
                else:
                    result = self._generate_video_bedrock(prompt, platform, image_path=resolved_image)
            except Exception as primary_err:
                err_msg = str(primary_err)
                print(f"[Media Service] Primary video generation failed: {err_msg}")
                # Fallback to Bedrock if Gemini failed but Bedrock client is available
                if (Config.GOOGLE_API_KEY or os.getenv('GOOGLE_API_KEY')) and self.bedrock_client:
                    print("[Media Service] Falling back to AWS Bedrock Nova Reel...")
                    try:
                        result = self._generate_video_bedrock(prompt, platform, image_path=resolved_image)
                    except Exception as fallback_err:
                        fallback_msg = str(fallback_err)
                        print(f"[Media Service] Bedrock video generation fallback failed: {fallback_msg}")
                        raise fallback_err
                elif "Access denied" in err_msg or "ResourceNotFoundException" in err_msg or "legacy" in err_msg.lower():
                    return {
                        "success": False,
                        "type": "video",
                        "platform": platform,
                        "error": (
                            "AWS Bedrock model access denied or model is legacy. "
                            "Please open your AWS Bedrock Console, navigate to 'Model access' in the left menu, "
                            "and request access for Amazon Nova Reel (for videos)."
                        )
                    }
                else:
                    raise primary_err

            # --- Single-Pass Native Video + Audio Optimization ---
            if result.get("url") and result.get("has_native_audio"):
                print("[Media Service] Single-pass native video+audio generated successfully. Skipping separate TTS audio merging.")
                return {
                    "success": True,
                    "type": "video",
                    "platform": platform,
                    "url": result["url"],
                    "prompt": prompt,
                    "provider": result.get("provider", "Google Gemini (Veo)"),
                    "has_native_audio": True,
                    "audio_mode": "single_pass_native"
                }

            # --- Video Post-processing for Silent Video Models (Fallback/AWS Bedrock) ---
            if result.get("url"):
                try:
                    # Resolve silent video path
                    silent_video_path = self._resolve_image_path(result["url"])
                    if silent_video_path and os.path.exists(silent_video_path):
                        # Extract dialogue for TTS
                        tts_text = self._extract_speech_dialogue(caption)
                        temp_audio_path = None
                        if tts_text:
                            from gtts import gTTS
                            tld_map = {
                                'b2b tech leader': 'co.uk',
                                'bold viral marketer': 'com',
                                'friendly lifestyle coach': 'com.au',
                                'high-growth startup': 'co.in',
                                'standard enterprise': 'ca',
                                'professional': 'co.uk',
                                'casual': 'com.au',
                                'enthusiastic': 'com',
                                'urgent': 'com',
                            }
                            tld_accent = tld_map.get((tone or '').lower(), 'com')
                            temp_audio_name = f"temp_tts_{uuid.uuid4().hex[:8]}.mp3"
                            temp_audio_path = os.path.join(self.upload_folder, temp_audio_name)
                            tts = gTTS(text=tts_text, lang='en', tld=tld_accent)
                            tts.save(temp_audio_path)

                        # Output path
                        processed_video_name = f"processed_{os.path.basename(silent_video_path)}"
                        processed_video_path = os.path.join(self.upload_folder, processed_video_name)

                        from moviepy.video.io.VideoFileClip import VideoFileClip
                        from moviepy.audio.io.AudioFileClip import AudioFileClip
                        
                        try:
                            from moviepy.video.fx.loop import loop
                        except ImportError:
                            try:
                                from moviepy.video.fx.all import loop
                            except ImportError:
                                loop = None

                        with VideoFileClip(silent_video_path) as video_clip:
                            # 1. Crop and resize to exactly 1080x1420 px
                            target_w, target_h = 1080, 1420
                            target_aspect = target_w / target_h
                            orig_w, orig_h = video_clip.w, video_clip.h
                            orig_aspect = orig_w / orig_h

                            if orig_aspect > target_aspect:
                                # Clip is wider than target aspect ratio. Keep height, crop width.
                                crop_h = orig_h
                                crop_w = int(orig_h * target_aspect)
                                x1 = (orig_w - crop_w) // 2
                                y1 = 0
                                x2 = x1 + crop_w
                                y2 = crop_h
                            else:
                                # Clip is taller than target aspect ratio. Keep width, crop height.
                                crop_w = orig_w
                                crop_h = int(orig_w / target_aspect)
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

                            if hasattr(video_cropped, 'resized'):
                                video_resized = video_cropped.resized((target_w, target_h))
                            else:
                                video_resized = video_cropped.resize((target_w, target_h))

                            # 2. Merge audio if available
                            if temp_audio_path and os.path.exists(temp_audio_path):
                                with AudioFileClip(temp_audio_path) as audio_clip:
                                    if audio_clip.duration > video_resized.duration and loop is not None:
                                        video_clip_looped = loop(video_resized, duration=audio_clip.duration)
                                        if hasattr(video_clip_looped, 'with_audio'):
                                            final_clip = video_clip_looped.with_audio(audio_clip)
                                        else:
                                            final_clip = video_clip_looped.set_audio(audio_clip)
                                    else:
                                        if hasattr(video_resized, 'with_audio'):
                                            video_trimmed = video_resized.with_duration(audio_clip.duration)
                                            final_clip = video_trimmed.with_audio(audio_clip)
                                        else:
                                            video_trimmed = video_resized.subclip(0, audio_clip.duration)
                                            final_clip = video_trimmed.set_audio(audio_clip)
                                    
                                    final_clip.write_videofile(
                                        processed_video_path,
                                        codec='libx264',
                                        audio_codec='aac',
                                        temp_audiofile=os.path.join(self.upload_folder, f"temp_{uuid.uuid4().hex[:8]}.m4a"),
                                        remove_temp=True,
                                        logger=None
                                    )
                            else:
                                # Just output the cropped/resized silent video
                                video_resized.write_videofile(
                                    processed_video_path,
                                    codec='libx264',
                                    logger=None
                                )

                        # Clean up temporary audio file
                        if temp_audio_path:
                            try:
                                os.remove(temp_audio_path)
                            except Exception:
                                pass
                                
                        # Replace the silent video file with the processed one
                        if os.path.exists(processed_video_path):
                            os.replace(processed_video_path, silent_video_path)
                            print(f"[Media Service] Video successfully cropped/resized to 1080x1420 px at {silent_video_path}")
                except Exception as merge_err:
                    print(f"[Media Service] Video post-processing failed: {merge_err}")

            return {
                "success": True,
                "type": "video",
                "platform": platform,
                "url": result["url"],
                "prompt": result["prompt"],
                "duration": result["duration"],
                "resolution": "1080x1420",
                "model": result["model"],
                "cost": result.get("cost"),
                "provider": self.media_provider,
                "source_image_url": f"/{resolved_image.replace(os.sep, '/').lstrip('/')}",
            }
        except Exception as e:
            return {
                "success": False,
                "type": "video",
                "platform": platform,
                "error": str(e),
            }

    # ── Video Storyboard Generation (fallback / reference) ─────────────────
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

    def _clean_text_for_tts(self, text: str) -> str:
        if not text:
            return ""
        # Remove hashtags
        words = [w for w in text.split() if not w.startswith('#')]
        cleaned = " ".join(words)
        import re
        cleaned = re.sub(r'https?://\S+', '', cleaned) # Remove URLs
        cleaned = re.sub(r'\[.*?\]', '', cleaned)      # Remove bracketed placeholders
        # Clean up double spaces
        cleaned = re.sub(r'\s+', ' ', cleaned)
        return cleaned.strip()

    def _enhance_image_prompt(self, user_caption: str, platform: str, tone: str = None) -> str:
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
            "into a highly detailed, visually rich, and professional prompt for image generation models (like Amazon Nova Canvas). "
            "Describe the scene in vivid detail: the main subject, clothing, environment/background, lighting (e.g. volumetric, warm golden hour, professional studio lighting), "
            "composition (e.g. medium shot, rule of thirds), camera details (e.g. shot on 35mm lens, shallow depth of field, sharp focus), and color palette. "
            "Keep the style realistic and photorealistic unless requested otherwise. "
            "Strictly avoid any text overlays, labels, or watermarks. "
            "SOURCE OF TRUTH ENFORCEMENT: The visual prompt must exactly represent the project and problem context given in the request. Do NOT invent or hallucinate features, projects, or problems. "
            "Output ONLY the final enhanced prompt in a single paragraph, under 500 characters."
        )
        
        user_prompt = f"Request: {user_caption}\nPlatform: {platform} ({platform_style}){tone_hint}"
        
        try:
            enhanced = self.llm_service.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.7,
                max_tokens=200
            )
            return enhanced.strip()[:500]
        except Exception as e:
            print(f"[Media Service] Image prompt enhancement failed: {e}. Using fallback.")
            return (
                f"A professional, photorealistic social media image for {platform.capitalize()}: {user_caption}. "
                f"Sleek visual composition, shallow depth of field, studio lighting, highly detailed."
            )

    def _compress_video_prompt(self, user_caption: str) -> str:
        """
        Compress a long user prompt/caption into a highly optimized visual motion prompt
        for video generation models (like Nova Reel), strictly under 400 characters.
        """
        system_prompt = (
            "You are an expert AI video prompt engineer. Your job is to compress a user's video generation request "
            "into a concise, highly descriptive motion and visual prompt suitable for video generation models (e.g. Amazon Nova Reel). "
            "Focus purely on: subject actions, character movements (like lip sync, speaking, hand gestures, head nods, eye contact), "
            "camera motion (like cinematic push-in, steady shot), and style. "
            "Remove all conversational meta-instructions, negations (do not say 'no text', 'no cuts'), and redundant words. "
            "SOURCE OF TRUTH ENFORCEMENT: The visual prompt must exactly represent the project and problem context given in the request. Do NOT invent or hallucinate features, projects, or problems. "
            "The output must be a single, continuous prompt, strictly under 400 characters."
        )
        try:
            compressed = self.llm_service.generate(
                system_prompt=system_prompt,
                user_prompt=user_caption,
                temperature=0.3,
                max_tokens=150
            )
            compressed_str = compressed.strip()
            return compressed_str[:400]
        except Exception as e:
            print(f"[Media Service] Prompt compression failed: {e}. Using fallback truncation.")
            return user_caption[:400]

    def _extract_speech_dialogue(self, caption: str) -> str:
        """
        Extract only the spoken dialogue or text that should be read aloud from a prompt.
        If no dialogue is specified, return the cleaned caption.
        """
        import re
        # Check for explicit 'Speak exactly this dialogue only: "..."' or similar pattern
        match = re.search(r'Speak\s+exactly\s+this\s+dialogue\s+only[:\s]*["“](.*?)["”]', caption, re.IGNORECASE | re.DOTALL)
        if match:
            extracted = match.group(1).strip().lstrip('—').strip()
            if extracted:
                return extracted

        system_prompt = (
            "You are an AI assistant. Extract ONLY the spoken dialogue, voiceover script, or text that should be "
            "spoken aloud from the user's prompt. Do not include any instructions, scene descriptions, metadata, "
            "or negative constraints. Return ONLY the exact dialogue text to be read by a text-to-speech reader. "
            "If the prompt contains dialogue quotes (e.g. Speak exactly this dialogue only: \"...\"), return "
            "only the content inside those quotes. If there is no specific dialogue, return a cleaned version "
            "of the prompt suitable for speaking."
        )
        try:
            dialogue = self.llm_service.generate(
                system_prompt=system_prompt,
                user_prompt=caption,
                temperature=0.1,
                max_tokens=300
            )
            clean_dialogue = dialogue.strip().replace('"', '').lstrip('—').strip()
            return clean_dialogue or self._clean_text_for_tts(caption)
        except Exception as e:
            print(f"[Media Service] Dialogue extraction failed: {e}")
            return self._clean_text_for_tts(caption)

