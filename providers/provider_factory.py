from __future__ import annotations

from typing import Any

from .base_provider import BaseProvider
from .openrouter_provider import OpenRouterProvider
from .openai_provider import OpenAICompatibleProvider
from .ollama_provider import OllamaProvider


class ProviderFactory:
    """
    Factory for creating LLM providers.
    """

    @staticmethod
    def create(
        *,
        provider_name: str,
        api_key: str | None = None,
        base_url: str | None = None,
        **kwargs: Any,
    ) -> BaseProvider:

        provider = provider_name.lower()

        if provider == "openrouter":
            return OpenRouterProvider(
                api_key=api_key,
                base_url=base_url
                or "https://openrouter.ai/api/v1/chat/completions",
                **kwargs,
            )

        if provider in ("openai", "openai-compatible"):
            return OpenAICompatibleProvider(
                api_key=api_key,
                base_url=base_url
                or "https://api.openai.com/v1/chat/completions",
                **kwargs,
            )

        if provider == "ollama":
            return OllamaProvider(
                base_url=base_url
                or "http://localhost:11434/v1/chat/completions",
                **kwargs,
            )

        raise ValueError(f"Unsupported provider: {provider_name}")