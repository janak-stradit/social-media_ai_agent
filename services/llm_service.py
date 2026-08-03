import openai
import json
from config import Config


class LLMService:
    """LLM client for agents — Bedrock (preferred), OpenRouter, or OpenAI with automatic failover and token/cost tracking."""

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

    def _calculate_cost(self, provider_name, model_name, in_tokens, out_tokens):
        """Calculate estimated cost USD based on provider and model rates"""
        in_tokens = max(0, int(in_tokens or 0))
        out_tokens = max(0, int(out_tokens or 0))
        
        # Rates per 1,000 tokens
        if "nova-lite" in model_name.lower():
            rate_in, rate_out = 0.00006, 0.00024
        elif "nova-pro" in model_name.lower() or "sonnet" in model_name.lower():
            rate_in, rate_out = 0.003, 0.015
        elif "gpt-4o-mini" in model_name.lower():
            rate_in, rate_out = 0.00015, 0.0006
        else:
            # Default fallback rate
            rate_in, rate_out = 0.0005, 0.0015

        cost = ((in_tokens / 1000.0) * rate_in) + ((out_tokens / 1000.0) * rate_out)
        return {
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "total_tokens": in_tokens + out_tokens,
            "cost_usd": round(cost, 6),
            "provider": provider_name,
            "model": model_name
        }

    def generate(self, system_prompt, user_prompt, temperature=0.7, max_tokens=1000, return_usage=False):
        """Generate text using available LLM providers in sequence with optimal token budgeting."""
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
                    text_out = response['output']['message']['content'][0]['text']
                    
                    usage_raw = response.get('usage', {})
                    in_t = usage_raw.get('inputTokens', len(system_prompt + user_prompt) // 4)
                    out_t = usage_raw.get('outputTokens', len(text_out) // 4)
                    usage_metrics = self._calculate_cost("bedrock", provider["model"], in_t, out_t)
                    
                    if return_usage:
                        return text_out, usage_metrics
                    return text_out
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
                    text_out = response.choices[0].message.content
                    
                    usage_raw = getattr(response, 'usage', None)
                    in_t = getattr(usage_raw, 'prompt_tokens', len(system_prompt + user_prompt) // 4) if usage_raw else len(system_prompt + user_prompt) // 4
                    out_t = getattr(usage_raw, 'completion_tokens', len(text_out) // 4) if usage_raw else len(text_out) // 4
                    usage_metrics = self._calculate_cost(provider["name"], provider["model"], in_t, out_t)

                    if return_usage:
                        return text_out, usage_metrics
                    return text_out
            except Exception as e:
                last_error = e
                print(f"[LLM Service] Provider {provider['name']} failed: {e}. Trying fallback...")
        raise Exception(f"LLM Generation failed for all providers. Last error: {str(last_error)}")

    def _robust_parse_json(self, content_str: str) -> dict:
        """Parse JSON response resiliently using json_repair to handle invalid control characters, unescaped quotes, and formatting glitches."""
        if not content_str:
            return {}
        
        cleaned = content_str.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned.split("```json")[1].split("```")[0].strip()
        elif cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1].split("```")[0].strip()

        # Attempt 1: Standard json.loads with strict=False
        try:
            return json.loads(cleaned, strict=False)
        except Exception:
            pass

        # Attempt 2: json_repair (fixes unescaped inner quotes, missing commas & control characters)
        try:
            import json_repair
            repaired = json_repair.repair_json(cleaned, return_objects=True)
            if isinstance(repaired, dict):
                return repaired
        except Exception:
            pass

        # Attempt 3: Demjson3 fallback
        try:
            import demjson3
            res = demjson3.decode(cleaned)
            if isinstance(res, dict):
                return res
        except Exception:
            pass

        # Attempt 4: Fallback parse
        return json.loads(cleaned)

    def generate_json(self, system_prompt, user_prompt, temperature=0.5, max_tokens=1200, return_usage=False):
        """Generate structured JSON response with optimal token budgeting."""
        last_error = None
        for provider in self.providers:
            try:
                if provider["name"] == "bedrock":
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
                    
                    usage_raw = response.get('usage', {})
                    in_t = usage_raw.get('inputTokens', len(json_system_prompt + user_prompt) // 4)
                    out_t = usage_raw.get('outputTokens', len(content) // 4)
                    usage_metrics = self._calculate_cost("bedrock", provider["model"], in_t, out_t)
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

                    usage_raw = getattr(response, 'usage', None)
                    in_t = getattr(usage_raw, 'prompt_tokens', len(system_prompt + user_prompt) // 4) if usage_raw else len(system_prompt + user_prompt) // 4
                    out_t = getattr(usage_raw, 'completion_tokens', len(content) // 4) if usage_raw else len(content) // 4
                    usage_metrics = self._calculate_cost(provider["name"], provider["model"], in_t, out_t)

                content_str = content.strip()
                parsed_json = self._robust_parse_json(content_str)

                if return_usage:
                    return parsed_json, usage_metrics
                return parsed_json
            except Exception as e:
                last_error = e
                print(f"[LLM Service] Provider {provider['name']} failed JSON generation: {e}. Trying fallback...")
        raise Exception(f"LLM JSON Generation failed for all providers. Last error: {str(last_error)}")