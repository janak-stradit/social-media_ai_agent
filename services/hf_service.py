import base64

import requests
from transformers import pipeline

from config import Config


class HuggingFaceService:
    def __init__(self):
        self.api_token = Config.HF_API_TOKEN
        self.headers = {"Authorization": f"Bearer {self.api_token}"}
        self.caption_pipeline = None

        # Image-to-text model endpoints (kept for legacy reference)
        self.caption_models = {
            "blip": "https://router.huggingface.co/hf-inference/models/Salesforce/blip-image-captioning-large",
            "vit-gpt2": "https://router.huggingface.co/hf-inference/models/nlpconnect/vit-gpt2-image-captioning",
            "blip-2": "https://router.huggingface.co/hf-inference/models/Salesforce/blip-image-captioning-base",
        }

        # Bedrock Vision Config
        self.vision_provider = getattr(Config, "VISION_PROVIDER", "local").lower()
        self.bedrock_client = None

        if self.vision_provider == "bedrock":
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
                print(
                    f"[HuggingFaceService] Bedrock client initialized with model: {getattr(Config, 'BEDROCK_VISION_MODEL', 'amazon.nova-lite-v1:0')}"
                )
            except Exception as e:
                print(f"[HuggingFaceService] Bedrock client initialization failed: {e}. Falling back to local.")

    def encode_image(self, image_path):
        """Convert image to base64 for API"""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def get_image_caption(self, image_path, model="blip"):
        """Get caption using local transformers library to bypass Inference API deprecation, or optionally via AWS Bedrock"""
        if self.vision_provider == "bedrock" and self.bedrock_client:
            try:
                with open(image_path, "rb") as f:
                    image_bytes = f.read()

                ext = image_path.rsplit(".", 1)[-1].lower()
                if ext == "jpg":
                    ext = "jpeg"
                if ext not in ["png", "jpeg", "gif", "webp"]:
                    ext = "jpeg"

                model_id = getattr(Config, "BEDROCK_VISION_MODEL", "amazon.nova-lite-v1:0")
                prompt = "Describe what is happening in this image in one clear, detailed sentence."

                response = self.bedrock_client.converse(
                    modelId=model_id,
                    messages=[
                        {
                            "role": "user",
                            "content": [{"image": {"format": ext, "source": {"bytes": image_bytes}}}, {"text": prompt}],
                        }
                    ],
                )

                output_content = response["output"]["message"]["content"]
                caption = "".join([block["text"] for block in output_content if "text" in block])
                return caption.strip()
            except Exception as bedrock_err:
                print(
                    f"[Bedrock Vision] Failed to generate caption via Bedrock: {bedrock_err}. Falling back to local/HF."
                )

        try:
            if self.caption_pipeline is None:
                # Use Salesforce/blip-image-captioning-base as a high-quality lightweight local model
                self.caption_pipeline = pipeline("image-to-text", model="Salesforce/blip-image-captioning-base")
            result = self.caption_pipeline(image_path, max_new_tokens=50)
            if isinstance(result, list) and len(result) > 0:
                return result[0].get("generated_text", "")
            return str(result)
        except Exception as local_err:
            # Fallback to API if local model loading fails (unlikely, but safe)
            try:
                with open(image_path, "rb") as f:
                    data = f.read()

                api_url = self.caption_models.get(model, self.caption_models["blip"])

                response = requests.post(api_url, headers=self.headers, data=data, timeout=30)

                if response.status_code == 200:
                    result = response.json()
                    if isinstance(result, list) and len(result) > 0:
                        return result[0].get("generated_text", "")
                    return str(result)
                else:
                    raise Exception(f"HF API Error: {response.status_code} - {response.text}") from local_err
            except Exception as api_err:
                raise Exception(
                    f"Failed to generate caption. Local error: {str(local_err)}. API error: {str(api_err)}"
                ) from api_err

    def get_image_features(self, image_path):
        """Extract visual features for content analysis"""
        if self.vision_provider == "bedrock" and self.bedrock_client:
            try:
                with open(image_path, "rb") as f:
                    image_bytes = f.read()

                ext = image_path.rsplit(".", 1)[-1].lower()
                if ext == "jpg":
                    ext = "jpeg"
                if ext not in ["png", "jpeg", "gif", "webp"]:
                    ext = "jpeg"

                model_id = getattr(Config, "BEDROCK_VISION_MODEL", "amazon.nova-lite-v1:0")
                prompt = "List the main visual features, objects, colors, composition, and mood of this image as a comma-separated list."

                response = self.bedrock_client.converse(
                    modelId=model_id,
                    messages=[
                        {
                            "role": "user",
                            "content": [{"image": {"format": ext, "source": {"bytes": image_bytes}}}, {"text": prompt}],
                        }
                    ],
                )

                output_content = response["output"]["message"]["content"]
                features = "".join([block["text"] for block in output_content if "text" in block])
                return features.strip()
            except Exception as bedrock_err:
                print(f"[Bedrock Vision] Failed to extract features via Bedrock: {bedrock_err}. Falling back to None.")

        # Since API fails, return None (handled gracefully by vision_agent.py)
        return None
