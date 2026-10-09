"""Chat (LLM) provider selection.

Chat runs on DeepSeek (``DEEPSEEK_API_KEY``, model ``deepseek-chat``).
OpenRouter is an optional fallback, used only when ``DEEPSEEK_API_KEY`` is
empty and ``OPENROUTER_API_KEY`` is set. Both expose an OpenAI-compatible API,
so the rest of the code keeps using ``AsyncOpenAI`` unchanged. Every chat call
goes through ``build_chat_client``/``chat_model``. Embeddings are chosen
separately in ``embeddings_provider`` (DeepSeek serves no embedding models).

Do not set ``DEEPSEEK_MODEL=deepseek-reasoner``: it does not support tool
calls, which the agent loop depends on.
"""

import os
from dataclasses import dataclass

import structlog
from openai import AsyncOpenAI

logger = structlog.get_logger(__name__)

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

    Raises ``ValueError`` when neither DEEPSEEK_API_KEY nor the optional
    OPENROUTER_API_KEY is set.
    """
    cfg = chat_provider_config()
    if not cfg.api_key:
        raise ValueError("No chat provider key set: define DEEPSEEK_API_KEY")
    if cfg.name != "deepseek":
        logger.warning(
            "DEEPSEEK_API_KEY is not set: chat runs on the OpenRouter fallback",
            model=cfg.model,
        )
    return AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
