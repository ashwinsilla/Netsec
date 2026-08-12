from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent.parent

DIRECTORIES = [
    ROOT / "providers",
    ROOT / "config",
]

FILES = {
    ROOT / "providers" / "__init__.py": '''"""
Provider package.
"""
''',

    ROOT / "providers" / "base_provider.py": '''from abc import ABC, abstractmethod


class BaseProvider(ABC):
    """
    Base interface for every LLM provider.
    """

    @abstractmethod
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        **kwargs,
    ):
        """
        Generate a response from the provider.
        """
        raise NotImplementedError
''',

    ROOT / "providers" / "openai_compatible_provider.py": '''from .base_provider import BaseProvider


class OpenAICompatibleProvider(BaseProvider):
    """
    OpenAI-compatible provider.

    This class will later support:
    - OpenRouter
    - Ollama
    - vLLM
    - LM Studio
    - OpenAI
    """

    def __init__(self, base_url, api_key=None):
        self.base_url = base_url
        self.api_key = api_key

    async def generate(
        self,
        system_prompt,
        user_prompt,
        model,
        **kwargs,
    ):
        raise NotImplementedError(
            "Implementation will be added in Milestone 3."
        )
''',

    ROOT / "providers" / "openrouter_provider.py": '''from .openai_compatible_provider import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    """
    OpenRouter implementation.
    """

    pass
''',

    ROOT / "providers" / "ollama_provider.py": '''from .openai_compatible_provider import OpenAICompatibleProvider


class OllamaProvider(OpenAICompatibleProvider):
    """
    Ollama implementation.
    """

    pass
''',

    ROOT / "providers" / "provider_factory.py": '''"""
Provider factory.

Returns the correct provider based on configuration.
"""

from .openrouter_provider import OpenRouterProvider
from .ollama_provider import OllamaProvider


class ProviderFactory:

    @staticmethod
    def create(provider_name, base_url, api_key=None):

        provider_name = provider_name.lower()

        if provider_name == "openrouter":
            return OpenRouterProvider(base_url, api_key)

        if provider_name == "ollama":
            return OllamaProvider(base_url)

        raise ValueError(f"Unknown provider: {provider_name}")
''',
}

JSON_FILES = {
    ROOT / "config" / "models.json": {
        "models": [
            "llama",
            "qwen",
            "glm",
            "kimi"
        ]
    },

    ROOT / "config" / "provider_config.json": {
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "",
        "timeout": 120
    }
}


def create_directory(path: Path):
    if not path.exists():
        path.mkdir(parents=True)
        print(f"[+] Created directory: {path.relative_to(ROOT)}")
    else:
        print(f"[=] Directory exists: {path.relative_to(ROOT)}")


def create_file(path: Path, content: str):
    if path.exists():
        print(f"[=] File exists: {path.relative_to(ROOT)}")
        return

    path.write_text(content, encoding="utf-8")
    print(f"[+] Created file: {path.relative_to(ROOT)}")


def create_json(path: Path, data):
    if path.exists():
        print(f"[=] File exists: {path.relative_to(ROOT)}")
        return

    path.write_text(
        json.dumps(data, indent=4),
        encoding="utf-8",
    )
    print(f"[+] Created file: {path.relative_to(ROOT)}")


def main():

    print("=" * 60)
    print("Milestone 2 - Provider Layer Setup")
    print("=" * 60)

    for directory in DIRECTORIES:
        create_directory(directory)

    for file_path, content in FILES.items():
        create_file(file_path, content)

    for file_path, content in JSON_FILES.items():
        create_json(file_path, content)

    print("\nSetup completed successfully.")
    print("Provider layer scaffold is ready.")


if __name__ == "__main__":
    main()