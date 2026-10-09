"""Outbound notification delivery (notifications.outbound -> provider).

Providers are MOCKED: the Gmail and WhatsApp send callables are AsyncMocks,
so no real email or WhatsApp message is sent. Everything else is real:

- The idempotency ledger runs against PostgreSQL using the actual
  migration 011 SQL, in a throwaway schema. Set OUTBOUND_TEST_DATABASE_URL
  (or DATABASE_URL) to a reachable server; otherwise these tests skip.
- ``test_kafka_topic_message_triggers_send`` additionally needs a broker at
  OUTBOUND_TEST_KAFKA (e.g. localhost:9092) and is marked ``integration``.
  It uses a throwaway copy of the outbound topic: publishing to the real
  ``notifications.outbound`` would let a running worker on the same broker
  consume the test event and try a real send.
"""

import asyncio
import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import asyncpg
import pytest

from kafka_client import DLQ_TOPIC, NOTIFICATIONS_OUTBOUND_TOPIC, KafkaMessage
from workers.notification_sender import OutboundSender

MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "database"
    / "migrations"
    / "011_outbound_deliveries.sql"
)


def _db_url() -> str | None:
    url = os.getenv("OUTBOUND_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        return None
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    url = _db_url()
    if not url:
        pytest.skip("no OUTBOUND_TEST_DATABASE_URL / DATABASE_URL")
    schema = f"outbound_test_{uuid.uuid4().hex[:8]}"
    try:
        admin = await asyncpg.connect(url, timeout=3)
    except (TimeoutError, OSError, asyncpg.PostgresError) as e:
        pytest.skip(f"PostgreSQL not reachable: {e}")
    await admin.execute(f"CREATE SCHEMA {schema}")
    p = await asyncpg.create_pool(
        url, min_size=1, max_size=4, server_settings={"search_path": schema}
    )
    async with p.acquire() as conn:
        # Minimal shapes of the tables the sender reads; the ledger table
        # comes from the real migration file.
        await conn.execute("""
            CREATE TABLE tickets (
                id UUID PRIMARY KEY, customer_id UUID NOT NULL, subject TEXT
            );
            CREATE TABLE customer_identifiers (
                customer_id UUID NOT NULL,
                identifier_type VARCHAR(50) NOT NULL,
                identifier_value VARCHAR(500) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
        await conn.execute(MIGRATION.read_text())
    try:
        yield p
    finally:
        await p.close()
        await admin.execute(f"DROP SCHEMA {schema} CASCADE")
        await admin.close()


async def _ticket(pool, subject="Cannot export CSV", phone=None) -> str:
    ticket_id, customer_id = uuid.uuid4(), uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tickets (id, customer_id, subject) VALUES ($1, $2, $3)",
            ticket_id,
            customer_id,
            subject,
        )
        if phone:
            await conn.execute(
                "INSERT INTO customer_identifiers VALUES ($1, 'phone', $2)",
                customer_id,
                phone,
            )
    return str(ticket_id)


def _event(ticket_id, channel="email", agent_run_id=None, **extra) -> KafkaMessage:
    payload = {
        "ticket_id": ticket_id,
        "customer_email": "jane@example.com",
        "channel": channel,
        "message": "Here is how to export your data.",
        "agent_run_id": agent_run_id or str(uuid.uuid4()),
    }
    payload.update(extra)
    return KafkaMessage(NOTIFICATIONS_OUTBOUND_TOPIC, payload)


async def _row(pool, agent_run_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM outbound_deliveries WHERE idempotency_key = $1",
            f"agent_run:{agent_run_id}",
        )


def _sender(pool, producer=None, **kw):
    producer = producer or AsyncMock()
    kw.setdefault("email_sender", AsyncMock())
    kw.setdefault("whatsapp_sender", AsyncMock(return_value="SM123"))
    return OutboundSender(pool, producer, max_attempts=3, retry_wait_seconds=0, **kw)


async def test_email_reply_is_sent_once_and_recorded(pool):
    ticket_id = await _ticket(pool)
    sender = _sender(pool)
    event = _event(ticket_id)

    assert await sender.handle(event) == "sent"
    sender._email_sender.assert_awaited_once_with(
        "jane@example.com", "Cannot export CSV", "Here is how to export your data."
    )
    row = await _row(pool, event.payload["agent_run_id"])
    assert row["status"] == "sent" and row["attempts"] == 1


async def test_redelivered_event_is_not_sent_twice(pool):
    ticket_id = await _ticket(pool)
    sender = _sender(pool)
    event = _event(ticket_id)

    assert await sender.handle(event) == "sent"
    # Same Kafka message delivered again (at-least-once).
    assert await sender.handle(event) == "duplicate"
    assert sender._email_sender.await_count == 1


async def test_whatsapp_reply_goes_to_customer_phone(pool):
    ticket_id = await _ticket(pool, phone="+15550001111")
    sender = _sender(pool)
    event = _event(ticket_id, channel="whatsapp")

    assert await sender.handle(event) == "sent"
    sender._whatsapp_sender.assert_awaited_once_with(
        "+15550001111", "Here is how to export your data."
    )
    row = await _row(pool, event.payload["agent_run_id"])
    assert row["provider_message_id"] == "SM123"


async def test_transient_error_is_retried_then_succeeds(pool):
    ticket_id = await _ticket(pool)
    email = AsyncMock(side_effect=[ConnectionError("reset"), None])
    sender = _sender(pool, email_sender=email)
    event = _event(ticket_id)

    assert await sender.handle(event) == "sent"
    assert email.await_count == 2
    assert (await _row(pool, event.payload["agent_run_id"]))["attempts"] == 2


async def test_exhausted_retries_mark_failed_and_dead_letter(pool):
    ticket_id = await _ticket(pool)
    producer = AsyncMock()
    email = AsyncMock(side_effect=ConnectionError("smtp down"))
    sender = _sender(pool, producer=producer, email_sender=email)
    event = _event(ticket_id)

    assert await sender.handle(event) == "failed"
    assert email.await_count == 3
    row = await _row(pool, event.payload["agent_run_id"])
    assert row["status"] == "failed" and "smtp down" in row["last_error"]

    producer.send_message.assert_awaited_once()
    topic, dlq_payload = producer.send_message.await_args.args[:2]
    assert topic == DLQ_TOPIC
    assert dlq_payload["original_topic"] == NOTIFICATIONS_OUTBOUND_TOPIC
    assert dlq_payload["original_payload"] == event.payload
    assert dlq_payload["attempts"] == 3

    # A failed delivery is final: redelivery does not retry the provider.
    assert await sender.handle(event) == "duplicate"
    assert email.await_count == 3


async def test_whatsapp_not_configured_is_permanent_failure(pool):
    ticket_id = await _ticket(pool, phone="+15550001111")
    whatsapp = AsyncMock(return_value="not-configured")
    sender = _sender(pool, whatsapp_sender=whatsapp)

    assert await sender.handle(_event(ticket_id, channel="whatsapp")) == "failed"
    # Not retried: missing configuration will not fix itself.
    assert whatsapp.await_count == 1


async def test_webform_reply_is_skipped_not_sent(pool):
    ticket_id = await _ticket(pool)
    sender = _sender(pool)
    event = _event(ticket_id, channel="webform")

    assert await sender.handle(event) == "skipped"
    sender._email_sender.assert_not_awaited()
    assert (await _row(pool, event.payload["agent_run_id"]))["status"] == "skipped"


async def test_mid_run_tool_event_is_superseded(pool):
    ticket_id = await _ticket(pool)
    sender = _sender(pool)
    # The send_response tool's event has no agent_run_id.
    event = _event(ticket_id)
    del event.payload["agent_run_id"]

    assert await sender.handle(event) == "superseded"
    sender._email_sender.assert_not_awaited()


@pytest.mark.integration
async def test_kafka_topic_message_triggers_send(pool):
    """Publish an outbound event to Kafka; the sender consumes and delivers it."""
    bootstrap = os.getenv("OUTBOUND_TEST_KAFKA")
    if not bootstrap:
        pytest.skip("OUTBOUND_TEST_KAFKA not set")

    from aiokafka.admin import AIOKafkaAdminClient, NewTopic

    from kafka_client import KafkaConsumerClient, KafkaProducerClient

    topic = f"{NOTIFICATIONS_OUTBOUND_TOPIC}.test-{uuid.uuid4().hex[:8]}"
    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
    await admin.start()
    # Create it up front so the first publish does not race auto-creation.
    await admin.create_topics([NewTopic(topic, num_partitions=1, replication_factor=1)])

    ticket_id = await _ticket(pool)
    agent_run_id = str(uuid.uuid4())
    handled = asyncio.Event()
    email = AsyncMock()

    producer = KafkaProducerClient(bootstrap)
    await producer.start()
    sender = OutboundSender(pool, producer, email_sender=email)

    async def on_message(message):
        await sender.handle(message)
        # Signal only after handle() has recorded the final status.
        if message.payload.get("agent_run_id") == agent_run_id:
            handled.set()

    # Fresh group so this test does not steal from a running worker.
    consumer = KafkaConsumerClient(bootstrap, f"outbound-test-{uuid.uuid4().hex[:6]}")
    await consumer.start([topic])
    task = asyncio.create_task(consumer.consume_messages(on_message))
    try:
        await producer.send_message(
            topic,
            {
                "ticket_id": ticket_id,
                "customer_email": "jane@example.com",
                "channel": "email",
                "message": f"reply for {agent_run_id}",
                "agent_run_id": agent_run_id,
            },
            key=ticket_id,
        )
        await asyncio.wait_for(handled.wait(), timeout=30)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await consumer.stop()
        await producer.stop()
        await admin.delete_topics([topic])
        await admin.close()

    email.assert_awaited_once_with(
        "jane@example.com", "Cannot export CSV", f"reply for {agent_run_id}"
    )
    row = await _row(pool, agent_run_id)
    assert row["status"] == "sent"


async def test_missing_gmail_token_fails_without_oauth_prompt(
    pool, tmp_path, monkeypatch
):
    # A bind-mounted token path that does not exist on the host shows up as
    # a directory; it must be a permanent failure, not an OAuth browser flow.
    monkeypatch.setenv("GMAIL_TOKEN_FILE", str(tmp_path))
    ticket_id = await _ticket(pool)
    producer = AsyncMock()
    sender = OutboundSender(pool, producer, max_attempts=3, retry_wait_seconds=0)
    event = _event(ticket_id)

    assert await sender.handle(event) == "failed"
    row = await _row(pool, event.payload["agent_run_id"])
    assert row["attempts"] == 1 and "token file" in row["last_error"]
    assert producer.send_message.await_args.args[0] == DLQ_TOPIC
