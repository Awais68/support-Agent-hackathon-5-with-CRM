"""Worker process for tests/test_inbound_redelivery.py (not a test module).

Runs the real consumer + MessageProcessor + agent against real Kafka and
PostgreSQL, with a scripted LLM and a producer that records to a file.
HARNESS_MODE picks where it stops so the test can SIGKILL it there:
  hang_in_agent     - block inside the LLM call
  hang_after_agent  - block after the agent finished, before the message
                      is marked done
  fail_once         - the first agent call raises (LLM outage), later
                      ones succeed
  normal            - process normally
"""

import asyncio
import inspect
import json
import os
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg

from agent.pre_processing_gate import GateAction, GateResult
from kafka_client import KafkaConsumerClient
from workers import message_processor as mp

MODE = os.environ["HARNESS_MODE"]
EVENTS = os.environ["HARNESS_EVENTS"]
REPLY = "You can reset your password from Settings > Security."


def record(event: dict) -> None:
    with open(EVENTS, "a") as f:
        f.write(json.dumps({"pid": os.getpid(), "mode": MODE, **event}, default=str) + "\n")


async def hang(label: str) -> None:
    record({"event": label})
    await asyncio.Event().wait()


class RecordingProducer:
    async def send_message(self, topic, payload, key=None):
        record({"event": "publish", "topic": topic, "payload": payload})
        return "recorded"


def _completion(content):
    message = MagicMock(content=content, tool_calls=None)
    message.model_dump.return_value = {"role": "assistant", "content": content}
    return MagicMock(choices=[MagicMock(message=message)], usage=None)


_failed = False


async def fake_llm(**kwargs):
    global _failed
    if "tools" not in kwargs:
        return _completion("Technical/Product")
    if MODE == "hang_in_agent":
        await hang("in_agent")
    if MODE == "fail_once" and not _failed:
        _failed = True
        record({"event": "llm_failed"})
        raise ConnectionError("LLM provider unreachable")
    return _completion(REPLY)


async def main() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=fake_llm)
    pool = await asyncpg.create_pool(os.environ["HARNESS_DB"], min_size=1, max_size=3)
    producer = RecordingProducer()

    patches = [
        patch.object(mp, "build_chat_client", return_value=client),
        patch.object(mp, "build_embedding_provider", return_value=None),
        patch(
            "agent.customer_success_agent.run_gate",
            AsyncMock(
                return_value=GateResult(action=GateAction.ALLOW, reason="ok", sentiment_score=0.5)
            ),
        ),
    ]
    if MODE == "hang_after_agent":
        real_done = getattr(mp.db, "mark_inbound_done", None)

        async def done_then_hang(*args, **kwargs):
            await hang("after_agent")
            if real_done:
                await real_done(*args, **kwargs)

        patches.append(patch.object(mp.db, "mark_inbound_done", new=done_then_hang, create=True))

    for p in patches:
        p.start()
    processor = mp.MessageProcessor(pool, cast(Any, producer))
    consumer = KafkaConsumerClient(os.environ["HARNESS_KAFKA"], os.environ["HARNESS_GROUP"])
    await consumer.start([os.environ["HARNESS_TOPIC"]])
    record({"event": "started"})

    kwargs: dict[str, Any] = {}
    params = inspect.signature(consumer.consume_messages).parameters
    if "dlq_producer" in params:
        kwargs = {"dlq_producer": producer, "retry_backoff_seconds": 0}

    async def on_message(message):
        await processor.process_message(message)
        record({"event": "handled", "message_id": message.message_id})

    await consumer.consume_messages(on_message, **kwargs)


if __name__ == "__main__":
    asyncio.run(main())
