#!/usr/bin/env python3
"""Live smoke test for the Gemini provider configured in .env.

Usage:
    .venv/bin/python scripts/test_gemini.py

The project uses Gemini for embeddings only — chat runs on OpenRouter — so only
the embedding checks are REQUIRED and decide the exit code. The chat checks are
INFO: they confirm what this key can do if you ever want to move chat over, and
they legitimately fail with HTTP 429 once the free daily chat quota is spent
without affecting embeddings.
"""

import asyncio
import sys

from openai import AsyncOpenAI

sys.path.insert(0, ".")

from database.queries import EMBEDDING_DIM  # noqa: E402
from embeddings_provider import build_embedding_provider  # noqa: E402
from env_config import load_environment  # noqa: E402

required_failures = 0


def record(name: str, ok: bool, detail: str = "", required: bool = True) -> None:
    global required_failures
    if not ok and required:
        required_failures += 1
    tag = "PASS" if ok else ("FAIL" if required else "SKIP")
    print(f"{tag}  {name}" + (f"  ::  {detail}" if detail else ""))


async def check_embeddings() -> None:
    provider = build_embedding_provider(None)
    if provider is None:
        record("embedding provider configured", False, "GEMINI_API_KEY not set")
        return
    record(
        "embedding provider configured",
        "generativelanguage.googleapis.com" in str(provider.client.base_url),
        f"{provider.model} @ {provider.client.base_url}",
    )

    # gemini-embedding-001 returns 3072 dimensions by default, but
    # knowledge_base.embedding is vector(1536) — the override is mandatory.
    try:
        vector = await provider.embed("how do I set up a data connector?")
        record(
            f"embedding dimension == {EMBEDDING_DIM}",
            len(vector) == EMBEDDING_DIM,
            f"got {len(vector)}",
        )
    except Exception as exc:
        record(f"embedding dimension == {EMBEDDING_DIM}", False, repr(exc)[:200])

    try:
        vectors = await provider.embed_many(["billing", "connectors", "exports"])
        record(
            "batch embeddings",
            len(vectors) == 3 and all(len(v) == EMBEDDING_DIM for v in vectors),
            f"{len(vectors)} vectors",
        )
    except Exception as exc:
        record("batch embeddings", False, repr(exc)[:200])


async def check_chat(model: str = "gemini-3.6-flash") -> None:
    """Informational: the project does not route chat through Gemini."""
    provider = build_embedding_provider(None)
    if provider is None:
        return
    client: AsyncOpenAI = provider.client

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly one word: PONG"}],
        )
        text = (resp.choices[0].message.content or "").strip()
        record(f"[info] chat completion ({model})", "PONG" in text.upper(), repr(text), required=False)
    except Exception as exc:
        record(f"[info] chat completion ({model})", False, repr(exc)[:160], required=False)

    tools = [{
        "type": "function",
        "function": {
            "name": "lookup_order",
            "description": "Look up an order by id",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
    }]
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Look up order ORD-991"}],
            tools=tools,
        )
        calls = resp.choices[0].message.tool_calls
        record(
            f"[info] tool calling ({model})",
            bool(calls),
            calls[0].function.arguments if calls else "no tool_calls",
            required=False,
        )
    except Exception as exc:
        record(f"[info] tool calling ({model})", False, repr(exc)[:160], required=False)


async def main() -> int:
    load_environment()
    print("-- required: embeddings (what this project actually uses) --")
    await check_embeddings()
    print("\n-- informational: chat (project uses OpenRouter for chat) --")
    await check_chat()

    print()
    if required_failures:
        print(f"{required_failures} required check(s) failed")
        return 1
    print("all required checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
