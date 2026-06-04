import httpx


class LLMClient:
    """
    Unified async client for OpenAI-compatible LLM APIs.
    Supports Qwen (DashScope), Kimi (Moonshot), and OpenAI.
    """

    ENDPOINTS = {
        "qwen":   "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "kimi":   "https://api.moonshot.cn/v1/chat/completions",
        "openai": "https://api.openai.com/v1/chat/completions",
    }
    DEFAULT_MODELS = {
        "qwen":   "qwen-max",
        "kimi":   "moonshot-v1-8k",
        "openai": "gpt-4o",
    }

    def __init__(self, engine: str, api_key: str, model: str = ""):
        if engine not in self.ENDPOINTS:
            raise ValueError(f"Unsupported engine: {engine}. Choose from: {list(self.ENDPOINTS)}")
        self.url   = self.ENDPOINTS[engine]
        self.key   = api_key
        self.model = model or self.DEFAULT_MODELS[engine]

    async def chat(self, system: str, user: str, temperature: float = 0.3) -> str:
        headers = {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(self.url, headers=headers, json=payload)
            resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    async def chat_with_image(self, system: str, user: str, image_b64: str,
                               media_type: str = "image/png") -> str:
        """Send a message with an inline base64 image (Vision models)."""
        headers = {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_b64}"}},
                    {"type": "text", "text": system + "\n\n" + user},
                ],
            }],
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(self.url, headers=headers, json=payload)
            resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
