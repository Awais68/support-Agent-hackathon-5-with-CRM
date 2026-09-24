"""Chat (LLM) provider selection.

DeepSeek is preferred when ``DEEPSEEK_API_KEY`` is set; otherwise chat falls
back to OpenRouter. Both expose an OpenAI-compatible API, so the rest of the
code keeps using ``AsyncOpenAI`` unchanged. Embeddings are unaffected — they
are chosen separately in ``embeddings_provider`` (DeepSeek serves no
embedding models).
"""

import os
from dataclasses import dataclass

from openai import AsyncOpenAI

DEEPSEEK_BASE_URL_DEFAULT = "https://api.deepseek.com"
DEEPSEEK_MODEL_DEFAULT = "deepseek-chat"
OPENROUTER_BASE_URL_DEFAULT = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL_DEFAULT = "openai/gpt-4o"


@dataclass(frozen=True)
class ChatProviderConfig:
    name: str
    api_key: str
    base_url: str
    model: str


def _env(name: str) -> str:
    """Env var with exported-but-empty treated as unset."""
    return (os.getenv(name) or "").strip()


def chat_provider_config() -> ChatProviderConfig:
    """Resolve the active chat provider from the environment."""
    deepseek_key = _env("DEEPSEEK_API_KEY")
    if deepseek_key:
        return ChatProviderConfig(
            name="deepseek",
            api_key=deepseek_key,
            base_url=_env("DEEPSEEK_BASE_URL") or DEEPSEEK_BASE_URL_DEFAULT,
            model=_env("DEEPSEEK_MODEL") or DEEPSEEK_MODEL_DEFAULT,
        )
    return ChatProviderConfig(
        name="openrouter",
        api_key=_env("OPENROUTER_API_KEY"),
        base_url=_env("OPENROUTER_BASE_URL") or OPENROUTER_BASE_URL_DEFAULT,
        model=_env("OPENAI_MODEL") or OPENROUTER_MODEL_DEFAULT,
    )


def chat_model() -> str:
    """Model name valid for the active chat provider."""
    return chat_provider_config().model


def build_chat_client() -> AsyncOpenAI:
    """OpenAI-compatible client for the active chat provider.

    Raises ``ValueError`` when neither DEEPSEEK_API_KEY nor OPENROUTER_API_KEY
    is set.
    """
    cfg = chat_provider_config()
    if not cfg.api_key:
        raise ValueError(
            "No chat provider key set: define DEEPSEEK_API_KEY or OPENROUTER_API_KEY"
        )
    return AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
