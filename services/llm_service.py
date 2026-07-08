import openai
from config import Config

class LLMService:
    def __init__(self):
        api_key = Config.OPENAI_API_KEY
        if api_key and (api_key.startswith("sk-or-") or "openrouter" in api_key.lower()):
            self.client = openai.OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1"
            )
        else:
            self.client = openai.OpenAI(api_key=api_key)
        self.model = Config.AGENTSCOPE_MODEL
    
    def generate(self, system_prompt, user_prompt, temperature=0.7, max_tokens=1000):
        """Generate text using OpenAI API"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=temperature,
                max_tokens=max_tokens
            )
            return response.choices[0].message.content
        except Exception as e:
            raise Exception(f"LLM Generation Error: {str(e)}")
    
    def generate_json(self, system_prompt, user_prompt, temperature=0.5, max_tokens=1000):
        """Generate structured JSON response"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"}
        )
        import json
        return json.loads(response.choices[0].message.content)