from __future__ import annotations

from .openai_provider import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    """
    OpenRouter provider.

    Uses the generic OpenAI-compatible implementation.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1/chat/completions",
        **kwargs,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            **kwargs,
        )