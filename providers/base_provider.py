from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import aiohttp


class BaseProvider(ABC):
    """
    Base interface for every LLM provider.

    Every backend (OpenRouter, Ollama, OpenAI, vLLM, LM Studio, etc.)
    must expose the same async generate() interface.
    """

    @abstractmethod
    async def generate(
        self,
        *,
        session: aiohttp.ClientSession,
        model: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> tuple[str | None, str | None]:
        """
        Execute a chat completion request.

        Args:
            session: Existing aiohttp session.
            model: Model name.
            messages: OpenAI-style messages.
            **kwargs: Provider-specific parameters.

        Returns:
            (response_text, error)
        """
        raise NotImplementedError