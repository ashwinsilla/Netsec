from __future__ import annotations

from .openai_provider import OpenAICompatibleProvider


class OllamaProvider(OpenAICompatibleProvider):
    """
    Ollama Provider.

    Ollama exposes an OpenAI-compatible Chat Completions API,
    so no custom HTTP implementation is required.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434/v1/chat/completions",
        **kwargs,
    ):
        super().__init__(
            api_key=None,
            base_url=base_url,
            **kwargs,
        )