"""N4: inbound messages are committed only once they are handled.

The consumer auto-committed every fetched offset and the handlers swallowed
every exception, so any failure (LLM outage, DB blip, worker restart) lost
the customer's message for good. Now: manual commits, in-process retries,
then a dead-letter event, and the offset is committed only after one of
those has happened.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiokafka import TopicPartition

from kafka_client import DLQ_TOPIC, KafkaConsumerClient, KafkaMessage, NonRetryableError

pytestmark = pytest.mark.asyncio


class _FakeConsumer:
    def __init__(self, raws):
        self._raws = list(raws)
        self.commit = AsyncMock()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._raws:
            raise StopAsyncIteration
        return self._raws.pop(0)


def _raw(offset=7, value=None, topic="inbound.email"):
    if value is None:
        value = KafkaMessage(topic, {"customer_email": "a@b.c"}, message_id="m-1").to_json()
    return SimpleNamespace(topic=topic, partition=0, offset=offset, value=value)


def _client(raws):
    client = KafkaConsumerClient("kafka:9092", "test-group")
    client.consumer = _FakeConsumer(raws)
    return client


def _committed(client):
    return [c.args[0] for c in client.consumer.commit.await_args_list]


async def test_auto_commit_is_off():
    with patch("kafka_client.AIOKafkaConsumer") as cls:
        cls.return_value.start = AsyncMock()
        await KafkaConsumerClient("kafka:9092", "g").start(["inbound.email"])
    assert cls.call_args.kwargs["enable_auto_commit"] is False


async def test_success_commits_next_offset():
    client = _client([_raw(offset=7)])
    handler = AsyncMock()
    await client.consume_messages(handler, dlq_producer=AsyncMock(), retry_backoff_seconds=0)
    handler.assert_awaited_once()
    assert _committed(client) == [{TopicPartition("inbound.email", 0): 8}]


async def test_failure_is_retried_then_dead_lettered_then_committed():
    client = _client([_raw(offset=3)])
    handler = AsyncMock(side_effect=RuntimeError("LLM down"))
    dlq = MagicMock(send_message=AsyncMock())
    await client.consume_messages(
        handler, dlq_producer=dlq, max_attempts=3, retry_backoff_seconds=0
    )
    assert handler.await_count == 3
    topic, payload = dlq.send_message.await_args.args[:2]
    assert topic == DLQ_TOPIC
    assert payload["original_topic"] == "inbound.email"
    assert payload["message_id"] == "m-1"
    assert payload["attempts"] == 3
    assert payload["payload"] == {"customer_email": "a@b.c"}
    assert "LLM down" in payload["error"]
    assert _committed(client) == [{TopicPartition("inbound.email", 0): 4}]


async def test_transient_failure_recovers_without_dlq():
    client = _client([_raw()])
    handler = AsyncMock(side_effect=[RuntimeError("blip"), None])
    dlq = MagicMock(send_message=AsyncMock())
    await client.consume_messages(handler, dlq_producer=dlq, retry_backoff_seconds=0)
    assert handler.await_count == 2
    dlq.send_message.assert_not_awaited()
    assert len(_committed(client)) == 1


async def test_non_retryable_goes_straight_to_dlq():
    client = _client([_raw()])
    handler = AsyncMock(side_effect=NonRetryableError("poison"))
    dlq = MagicMock(send_message=AsyncMock())
    await client.consume_messages(
        handler, dlq_producer=dlq, max_attempts=5, retry_backoff_seconds=0
    )
    assert handler.await_count == 1
    dlq.send_message.assert_awaited_once()
    assert len(_committed(client)) == 1


async def test_unparseable_message_is_dead_lettered_and_committed():
    client = _client([_raw(value="{not json")])
    handler = AsyncMock()
    dlq = MagicMock(send_message=AsyncMock())
    await client.consume_messages(handler, dlq_producer=dlq, retry_backoff_seconds=0)
    handler.assert_not_awaited()
    payload = dlq.send_message.await_args.args[1]
    assert payload["raw_value"] == "{not json"
    assert len(_committed(client)) == 1


async def test_dlq_failure_does_not_commit():
    """If the dead letter cannot be written, keep the offset so it redelivers."""
    client = _client([_raw()])
    handler = AsyncMock(side_effect=RuntimeError("LLM down"))
    dlq = MagicMock(send_message=AsyncMock(side_effect=ConnectionError("kafka gone")))
    with pytest.raises(ConnectionError):
        await client.consume_messages(
            handler, dlq_producer=dlq, max_attempts=2, retry_backoff_seconds=0
        )
    assert _committed(client) == []


async def test_processor_propagates_failures():
    """process_message must raise so the consumer can retry / dead-letter."""
    from workers.message_processor import MessageProcessor

    with (
        patch("workers.message_processor.build_chat_client"),
        patch("workers.message_processor.build_embedding_provider"),
    ):
        processor = MessageProcessor(MagicMock(), MagicMock())
    message = KafkaMessage("inbound.email", {"customer_email": "a@b.c"}, message_id="m-9")
    with (
        patch(
            "workers.message_processor.db.claim_inbound_message",
            AsyncMock(
                return_value={
                    "status": "processing",
                    "attempts": 1,
                    "ticket_id": None,
                    "created_at": None,
                }
            ),
            create=True,
        ),
        patch("workers.message_processor.db.record_inbound_error", AsyncMock(), create=True),
        patch.object(processor, "_process_email_message", AsyncMock(side_effect=RuntimeError("x"))),
        patch("workers.message_processor.build_agent", AsyncMock()),
        pytest.raises(RuntimeError),
    ):
        await processor.process_message(message)
