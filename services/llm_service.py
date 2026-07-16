import openai
import json
from config import Config


class LLMService:
    """LLM client for agents — Bedrock (preferred), OpenRouter, or OpenAI with automatic failover."""

    def __init__(self):
        api_key = Config.OPENAI_API_KEY
        self.openrouter_key = api_key if (api_key and (api_key.startswith("sk-or-") or "openrouter" in api_key.lower())) else None
        self.openai_key = api_key if (api_key and not self.openrouter_key) else None

        # AWS Bedrock initialization
        self.bedrock_client = None
        self.bedrock_model = getattr(Config, 'BEDROCK_TEXT_MODEL', 'amazon.nova-lite-v1:0')
        
        aws_access_key = getattr(Config, 'AWS_ACCESS_KEY_ID', None)
        aws_secret_key = getattr(Config, 'AWS_SECRET_ACCESS_KEY', None)
        aws_profile = getattr(Config, 'AWS_PROFILE', None)
        aws_region = getattr(Config, 'AWS_REGION', 'us-east-1')
        
        if aws_profile or (aws_access_key and aws_secret_key):
            try:
                import boto3
                from botocore.config import Config as BotoConfig
                
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
                    read_timeout=120,
                    connect_timeout=30,
                    retries={"max_attempts": 3}
                )
                self.bedrock_client = session.client(
                    "bedrock-runtime",
                    config=boto_config
                )
                print(f"[LLM Service] AWS Bedrock client initialized successfully using model {self.bedrock_model}.")
            except Exception as e:
                print(f"[LLM Service] Bedrock initialization failed: {e}")

        self.providers = []

        if self.bedrock_client:
            self.providers.append({
                "name": "bedrock",
                "client": self.bedrock_client,
                "model": self.bedrock_model
            })

        if self.openrouter_key:
            self.providers.append({
                "name": "openrouter",
                "client": openai.OpenAI(api_key=self.openrouter_key, base_url="https://openrouter.ai/api/v1"),
                "model": Config.AGENTSCOPE_MODEL
            })

        if self.openai_key or (api_key and not self.openrouter_key):
            self.providers.append({
                "name": "openai",
                "client": openai.OpenAI(api_key=api_key),
                "model": Config.AGENTSCOPE_MODEL
            })

        if not self.providers:
            raise RuntimeError("No LLM providers configured. Check your API keys and AWS credentials.")

    def generate(self, system_prompt, user_prompt, temperature=0.7, max_tokens=1000):
        """Generate text using available LLM providers in sequence"""
        last_error = None
        for provider in self.providers:
            try:
                if provider["name"] == "bedrock":
                    inference_config = {}
                    if temperature is not None:
                        inference_config["temperature"] = temperature
                    if max_tokens is not None:
                        inference_config["maxTokens"] = max_tokens
                    
                    response = provider["client"].converse(
                        modelId=provider["model"],
                        messages=[
                            {
                                "role": "user",
                                "content": [{"text": user_prompt}]
                            }
                        ],
                        system=[
                            {"text": system_prompt}
                        ],
                        inferenceConfig=inference_config
                    )
                    return response['output']['message']['content'][0]['text']
                else:
                    response = provider["client"].chat.completions.create(
                        model=provider["model"],
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=temperature,
                        max_tokens=max_tokens
                    )
                    return response.choices[0].message.content
            except Exception as e:
                last_error = e
                print(f"[LLM Service] Provider {provider['name']} failed: {e}. Trying fallback...")
        raise Exception(f"LLM Generation failed for all providers. Last error: {str(last_error)}")

    def generate_json(self, system_prompt, user_prompt, temperature=0.5, max_tokens=1000):
        """Generate structured JSON response using available LLM providers in sequence"""
        last_error = None
        for provider in self.providers:
            try:
                if provider["name"] == "bedrock":
                    # Instruct Bedrock models to output JSON
                    json_system_prompt = system_prompt
                    if "json" not in system_prompt.lower():
                        json_system_prompt += "\n\nYou must return your response ONLY as a valid JSON object. Do not include any explanations or markdown formatting outside the JSON."
                    
                    inference_config = {}
                    if temperature is not None:
                        inference_config["temperature"] = temperature
                    if max_tokens is not None:
                        inference_config["maxTokens"] = max_tokens
                    
                    response = provider["client"].converse(
                        modelId=provider["model"],
                        messages=[
                            {
                                "role": "user",
                                "content": [{"text": user_prompt}]
                            }
                        ],
                        system=[
                            {"text": json_system_prompt}
                        ],
                        inferenceConfig=inference_config
                    )
                    content = response['output']['message']['content'][0]['text']
                else:
                    kwargs = {
                        "model": provider["model"],
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        "temperature": temperature,
                        "max_tokens": max_tokens
                    }
                    kwargs["response_format"] = {"type": "json_object"}
                    response = provider["client"].chat.completions.create(**kwargs)
                    content = response.choices[0].message.content

                # Clean up any potential markdown formatting in case the model returns it
                content_str = content.strip()
                if content_str.startswith("```json"):
                    content_str = content_str.split("```json")[1].split("```")[0].strip()
                elif content_str.startswith("```"):
                    content_str = content_str.split("```")[1].split("```")[0].strip()

                return json.loads(content_str)
            except Exception as e:
                last_error = e
                print(f"[LLM Service] Provider {provider['name']} failed JSON generation: {e}. Trying fallback...")
        raise Exception(f"LLM JSON Generation failed for all providers. Last error: {str(last_error)}")