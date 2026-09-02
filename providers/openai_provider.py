from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .base_provider import BaseProvider


class OpenAICompatibleProvider(BaseProvider):
    """
    Generic provider for any OpenAI-compatible Chat Completions API.

    Works with:
    - OpenAI
    - OpenRouter
    - Ollama
    - LM Studio
    - vLLM
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        timeout: int = 180,
        max_retries: int = 3,
        retry_delay: int = 5,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    async def generate(
        self,
        *,
        session: aiohttp.ClientSession,
        model: str,
        user_message: str,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> tuple[str | None, str | None]:
        """
        Generate a completion using an OpenAI-compatible endpoint.

        Returns:
            (response_text, error)
        """

        headers = {
            "Content-Type": "application/json",
        }

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        messages = []

        if system_prompt:
            messages.append(
                {
                    "role": "system",
                    "content": system_prompt,
                }
            )

        messages.append(
            {
                "role": "user",
                "content": user_message,
            }
        )

        payload = {
            "model": model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.0),
            "max_tokens": kwargs.get("max_tokens", 2000),
        }

        for attempt in range(1, self.max_retries + 1):
            try:
                async with session.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as response:

                    if response.status == 429:
                        if attempt < self.max_retries:
                            await asyncio.sleep(self.retry_delay * attempt)
                            continue

                    if response.status != 200:
                        body = await response.text()
                        return None, f"HTTP {response.status}: {body[:500]}"

                    data = await response.json()

                    try:
                        text = data["choices"][0]["message"]["content"]
                    except Exception:
                        return None, f"Unexpected API response: {data}"

                    return text, None

            except asyncio.TimeoutError:
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay * attempt)
                    continue
                return None, "Request timed out."

            except Exception as e:
                return None, str(e)

        return None, f"Failed after {self.max_retries} retries."