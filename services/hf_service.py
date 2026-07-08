import requests
from PIL import Image
import io
import base64
from config import Config
from transformers import pipeline

class HuggingFaceService:
    def __init__(self):
        self.api_token = Config.HF_API_TOKEN
        self.headers = {"Authorization": f"Bearer {self.api_token}"}
        self.caption_pipeline = None
        
        # Image-to-text model endpoints (kept for legacy reference)
        self.caption_models = {
            'blip': 'https://router.huggingface.co/hf-inference/models/Salesforce/blip-image-captioning-large',
            'vit-gpt2': 'https://router.huggingface.co/hf-inference/models/nlpconnect/vit-gpt2-image-captioning',
            'blip-2': 'https://router.huggingface.co/hf-inference/models/Salesforce/blip-image-captioning-base'
        }
    
    def encode_image(self, image_path):
        """Convert image to base64 for API"""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode('utf-8')
    
    def get_image_caption(self, image_path, model='blip'):
        """Get caption using local transformers library to bypass Inference API deprecation"""
        try:
            if self.caption_pipeline is None:
                # Use Salesforce/blip-image-captioning-base as a high-quality lightweight local model
                self.caption_pipeline = pipeline("image-to-text", model="Salesforce/blip-image-captioning-base")
            result = self.caption_pipeline(image_path)
            if isinstance(result, list) and len(result) > 0:
                return result[0].get('generated_text', '')
            return str(result)
        except Exception as local_err:
            # Fallback to API if local model loading fails (unlikely, but safe)
            try:
                with open(image_path, "rb") as f:
                    data = f.read()
                
                api_url = self.caption_models.get(model, self.caption_models['blip'])
                
                response = requests.post(
                    api_url,
                    headers=self.headers,
                    data=data
                )
                
                if response.status_code == 200:
                    result = response.json()
                    if isinstance(result, list) and len(result) > 0:
                        return result[0].get('generated_text', '')
                    return str(result)
                else:
                    raise Exception(f"HF API Error: {response.status_code} - {response.text}")
            except Exception as api_err:
                raise Exception(f"Failed to generate caption. Local error: {str(local_err)}. API error: {str(api_err)}")
    
    def get_image_features(self, image_path):
        """Extract visual features for content analysis"""
        # Since API fails, return None (handled gracefully by vision_agent.py)
        return None