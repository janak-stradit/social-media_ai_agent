========================================================================
 GEMINI MULTIMODAL (IMAGE / VIDEO / AUDIO UNDERSTANDING) INTEGRATION
 via Google Cloud Vertex AI
========================================================================

CONTEXT
-------
Today, agents/vision_agent.py analyzes images in two hops:
  1. services/hf_service.py gets a caption from a HuggingFace model.
  2. services/llm_service.py (Bedrock / OpenRouter / OpenAI) reasons over
     that caption text -- it never sees the actual image.

This project already talks to Gemini once, in
services/media_service.py (_generate_google_gemini_video, ~line 855),
using the "google-genai" SDK authenticated with a plain GOOGLE_API_KEY
(AI Studio auth) to call Veo for video generation.

This guide adds TRUE multimodal understanding: a single Gemini call that
takes raw image/video/audio bytes directly and returns rich analysis
JSON, authenticated the "Google Cloud" way (Vertex AI: service account /
ADC + project + region), not just an AI Studio API key.


PHASE 1 -- GOOGLE CLOUD PROJECT SETUP
--------------------------------------
1. Create or select a project at https://console.cloud.google.com
2. Enable billing on the project (required for Vertex AI, even under
   free-tier usage).
3. Enable the Vertex AI API:

     gcloud config set project YOUR_PROJECT_ID
     gcloud services enable aiplatform.googleapis.com

4. Pick a region that serves Gemini models, e.g. us-central1.


PHASE 2 -- AUTHENTICATION
--------------------------
Choose ONE of the following:

  A) Local development (simplest):

       gcloud auth application-default login

     This writes Application Default Credentials (ADC) to your machine.
     No key file to manage or ship.

  B) Server / production (matches Dockerfile + docker-compose.yml):

     Create a service account with the aiplatform.user role, download
     its JSON key, and mount it into the container.

       gcloud iam service-accounts create gemini-vision-sa

       gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
         --member="serviceAccount:gemini-vision-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
         --role="roles/aiplatform.user"

       gcloud iam service-accounts keys create gemini-key.json \
         --iam-account=gemini-vision-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com

     Then set GOOGLE_APPLICATION_CREDENTIALS to point at gemini-key.json
     (see Phase 3). Make sure gemini-key.json is covered by .gitignore
     and never committed.


PHASE 3 -- ENVIRONMENT VARIABLES
----------------------------------
Add to .env (alongside the existing GOOGLE_API_KEY used for Veo):

    GOOGLE_GENAI_USE_VERTEXAI=true
    GOOGLE_CLOUD_PROJECT=your-project-id
    GOOGLE_CLOUD_LOCATION=us-central1
    GOOGLE_APPLICATION_CREDENTIALS=/path/to/gemini-key.json   # server/prod only
    GEMINI_VISION_MODEL=gemini-2.5-flash

Then add these to config.py, next to the existing GEMINI_VIDEO_MODEL
line (config.py line ~22):

    GOOGLE_CLOUD_PROJECT = os.getenv('GOOGLE_CLOUD_PROJECT')
    GOOGLE_CLOUD_LOCATION = os.getenv('GOOGLE_CLOUD_LOCATION', 'us-central1')
    GEMINI_VISION_MODEL = os.getenv('GEMINI_VISION_MODEL', 'gemini-2.5-flash')


PHASE 4 -- DEPENDENCIES
-------------------------
google-genai is already used (imported lazily) in media_service.py but
is not pinned in requirements.txt. Add these lines to requirements.txt:

    google-genai>=0.3.0
    google-cloud-aiplatform>=1.60.0

Then install:

    pip install -r requirements.txt


PHASE 5 -- NEW SERVICE FILE
-----------------------------
Create services/gemini_vision_service.py:

    import json
    from config import Config


    class GeminiVisionService:
        """Multimodal (image/video/audio) understanding via Gemini on Vertex AI."""

        def __init__(self):
            import google.genai as genai
            self.client = genai.Client(
                vertexai=True,
                project=Config.GOOGLE_CLOUD_PROJECT,
                location=Config.GOOGLE_CLOUD_LOCATION,
            )
            self.model = Config.GEMINI_VISION_MODEL

        def analyze_image(self, image_path: str, mime_type: str = "image/jpeg") -> dict:
            from google.genai import types

            with open(image_path, "rb") as f:
                image_bytes = f.read()

            prompt = """You are a Vision Content Agent. Analyze this image and return JSON with keys:
    rich_description, mood, colors, composition, story_angles (array of 3), platform_fit."""

            response = self.client.models.generate_content(
                model=self.model,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    prompt,
                ],
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            return json.loads(response.text)


PHASE 6 -- WIRE INTO vision_agent.py
---------------------------------------
Keep the existing HF + LLM path as a fallback, matching the same
try/except failover pattern already used in media_service.py
(generate_video, ~line 940-971):

    from services.gemini_vision_service import GeminiVisionService

    class VisionAgent:
        def __init__(self):
            self.hf = HuggingFaceService()
            self.llm = LLMService()
            self.gemini = GeminiVisionService()

        def analyze_image(self, image_path):
            try:
                return self.gemini.analyze_image(image_path)
            except Exception as e:
                print(f"[VisionAgent] Gemini analysis failed: {e}. Falling back to HF+LLM.")
                # existing HF caption + LLM enrichment code stays as-is
                ...


PHASE 7 -- OPTIONAL: VIDEO / AUDIO SUPPORT
---------------------------------------------
config.py's ALLOWED_EXTENSIONS (line ~10) is currently image-only:

    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

To let Gemini analyze uploaded video/audio too, add 'mp4', 'mov', 'mp3',
'wav', and rename analyze_image to something like analyze_media, passing
the correct mime_type through.


PHASE 8 -- TEST
-----------------
    gcloud auth application-default print-access-token   # sanity-check auth

    python -c "from services.gemini_vision_service import GeminiVisionService as G; print(G().analyze_image('static/uploads/some_test.jpg'))"


PHASE 9 -- COST / QUOTA NOTES
--------------------------------
Vertex AI Gemini is billed per input/output token (image tokens are
counted per-tile), separately from your existing Bedrock / OpenAI spend.
Check quotas under "Vertex AI API" in Cloud Console if you expect high
volume, and request a quota increase before launch if needed.
