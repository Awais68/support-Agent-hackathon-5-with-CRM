"""Deliver agent replies from ``notifications.outbound`` to the customer.

The agent publishes its final reply to ``notifications.outbound``; this
consumer turns that event into a Gmail or WhatsApp send.

Delivery guarantees:
- Idempotent: each reply is claimed in ``outbound_deliveries`` (migration
  011) before the provider is called, under ``inbound:<message id>`` (the
  inbound message being answered) or, for events without one,
  ``agent_run:<id>``. A redelivered Kafka message, or an agent re-run after
  a worker crash, is not sent twice.
- Retried: provider errors are retried with exponential backoff up to
  ``OUTBOUND_MAX_ATTEMPTS`` (default 3).
- Dead-lettered: after the last attempt the row is marked ``failed`` and the
  original event is published to the DLQ topic.
- Customer-voiced only: just ``customer_reply`` is delivered, never the
  agent's ``internal_note``. An empty or operator-voiced reply is not sent;
  the row is marked ``failed`` and the ticket escalated for a human.

Only events carrying ``agent_run_id`` are delivered. The ``send_response``
tool also publishes to this topic mid-run, but the agent always publishes
the final formatted reply at the end of the same run; sending both would
message the customer twice.
"""

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from agent.reply_guard import operator_voice_reason
from exceptions import sanitize_error_message
from kafka_client import (
    DLQ_TOPIC,
    NOTIFICATIONS_OUTBOUND_TOPIC,
    KafkaConsumerClient,
    KafkaMessage,
)

logger = structlog.get_logger(__name__)

CONSUMER_GROUP = "techflow-notification-sender"

# Channels whose reply is read in-app (support form / voice UI) rather than
# pushed to an external provider.
IN_APP_CHANNELS = {"webform", "web_form", "voice"}


class PermanentDeliveryError(Exception):
    """Delivery cannot succeed by retrying (missing config or recipient)."""


class OutboundSender:
    """Consumes outbound notification events and sends them via a channel."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        kafka_producer: Any,
        email_sender: Callable[[str, str, str], Awaitable[Any]] | None = None,
        whatsapp_sender: Callable[[str, str], Awaitable[Any]] | None = None,
        max_attempts: int | None = None,
        retry_wait_seconds: float | None = None,
    ):
        self.db_pool = db_pool
        self.kafka_producer = kafka_producer
        self._email_sender = email_sender
        self._whatsapp_sender = whatsapp_sender
        self.max_attempts = max_attempts or int(os.getenv("OUTBOUND_MAX_ATTEMPTS", "3"))
        self.retry_wait_seconds = (
            retry_wait_seconds
            if retry_wait_seconds is not None
            else float(os.getenv("OUTBOUND_RETRY_WAIT_SECONDS", "2"))
        )

    # ------------------------------------------------------------------
    # Provider clients (lazy, so a worker without Gmail/Twilio still starts)
    # ------------------------------------------------------------------
    def _get_email_sender(self) -> Callable[[str, str, str], Awaitable[Any]]:
        if self._email_sender is None:
            from channels.gmail_handler import GmailHandler

            handler = GmailHandler(self.kafka_producer)
            # GmailHandler.authenticate() falls back to an interactive browser
            # OAuth flow when there is no token, which would hang a container.
            # (isfile: a missing bind-mount source becomes a directory.)
            if not os.path.isfile(handler.token_file):
                raise PermanentDeliveryError(f"Gmail token file {handler.token_file!r} not found")
            self._email_sender = handler.send_reply
        return self._email_sender

    def _get_whatsapp_sender(self) -> Callable[[str, str], Awaitable[Any]]:
        if self._whatsapp_sender is None:
            from channels.whatsapp_handler import WhatsAppHandler

            self._whatsapp_sender = WhatsAppHandler(self.kafka_producer).send_message
        return self._whatsapp_sender

    # ------------------------------------------------------------------
    # Idempotency ledger
    # ------------------------------------------------------------------
    async def _claim(self, key: str, ticket_id: UUID | None, channel: str) -> bool:
        """Claim ``key`` for delivery; False if it already reached a final state."""
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO outbound_deliveries (idempotency_key, ticket_id, channel)
                VALUES ($1, $2, $3)
                ON CONFLICT (idempotency_key) DO UPDATE
                    SET updated_at = CURRENT_TIMESTAMP
                    WHERE outbound_deliveries.status = 'pending'
                RETURNING idempotency_key
                """,
                key,
                ticket_id,
                channel,
            )
        return row is not None

    async def _finish(
        self,
        key: str,
        status: str,
        attempts: int,
        provider_message_id: str | None = None,
        error: str | None = None,
    ) -> None:
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE outbound_deliveries
                SET status = $2, attempts = $3, provider_message_id = $4,
                    last_error = $5, updated_at = CURRENT_TIMESTAMP
                WHERE idempotency_key = $1
                """,
                key,
                status,
                attempts,
                provider_message_id,
                error,
            )

    # ------------------------------------------------------------------
    # Recipient lookup
    # ------------------------------------------------------------------
    async def _ticket_subject(self, ticket_id: UUID | None) -> str:
        if ticket_id is None:
            return "Your support request"
        async with self.db_pool.acquire() as conn:
            subject = await conn.fetchval("SELECT subject FROM tickets WHERE id = $1", ticket_id)
        return subject or "Your support request"

    async def _customer_phone(self, ticket_id: UUID | None) -> str:
        if ticket_id is None:
            raise PermanentDeliveryError("WhatsApp reply has no ticket_id")
        async with self.db_pool.acquire() as conn:
            phone = await conn.fetchval(
                """
                SELECT ci.identifier_value
                FROM tickets t
                JOIN customer_identifiers ci ON ci.customer_id = t.customer_id
                WHERE t.id = $1 AND ci.identifier_type = 'phone'
                ORDER BY ci.created_at DESC
                LIMIT 1
                """,
                ticket_id,
            )
        if not phone:
            raise PermanentDeliveryError("customer has no phone identifier")
        return phone

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------
    async def _send_once(self, channel: str, payload: dict, ticket_id: UUID | None) -> str | None:
        body = payload.get("customer_reply") or ""

        if channel in ("email", "gmail"):
            to_email = payload.get("customer_email")
            if not to_email:
                raise PermanentDeliveryError("email reply has no customer_email")
            sender = self._get_email_sender()
            await sender(to_email, await self._ticket_subject(ticket_id), body)
            return None

        if channel == "whatsapp":
            sender = self._get_whatsapp_sender()
            result = await sender(await self._customer_phone(ticket_id), body)
            # WhatsAppHandler reports these as return values, not exceptions.
            if result == "not-configured":
                raise PermanentDeliveryError("Twilio WhatsApp is not configured")
            if result == "circuit-open":
                raise RuntimeError("Twilio circuit breaker is open")
            return str(result) if result else None

        raise PermanentDeliveryError(f"unsupported channel {channel!r}")

    async def handle(self, message: KafkaMessage) -> str:
        """Deliver one event. Returns the final status for logging/tests."""
        payload = message.payload
        agent_run_id = payload.get("agent_run_id")
        channel = (payload.get("channel") or "").lower()

        if not agent_run_id:
            logger.debug(
                "Outbound event superseded by final agent reply",
                message_id=message.message_id,
            )
            return "superseded"

        try:
            ticket_id = UUID(payload["ticket_id"]) if payload.get("ticket_id") else None
        except ValueError:
            ticket_id = None

        # One reply per inbound message: after a crash the worker may re-run
        # the agent (new agent_run_id) for the same inbound message.
        source_message_id = payload.get("source_message_id")
        key = f"inbound:{source_message_id}" if source_message_id else f"agent_run:{agent_run_id}"
        if not await self._claim(key, ticket_id, channel or "unknown"):
            logger.info("Outbound reply already handled", idempotency_key=key)
            return "duplicate"

        if channel in IN_APP_CHANNELS:
            await self._finish(key, "skipped", 0, error="in-app channel")
            return "skipped"

        blocked = operator_voice_reason(payload.get("customer_reply"))
        if blocked:
            error = f"blocked: {blocked}"
            await self._finish(key, "failed", 0, error=error)
            await self._escalate(ticket_id)
            logger.warning(
                "Outbound reply blocked; ticket escalated",
                idempotency_key=key,
                channel=channel,
                reason=blocked,
            )
            return "blocked"

        attempts = 0
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self.max_attempts),
                wait=wait_exponential(
                    multiplier=self.retry_wait_seconds, max=30 * self.retry_wait_seconds
                ),
                retry=retry_if_not_exception_type(PermanentDeliveryError),
                reraise=True,
            ):
                with attempt:
                    attempts += 1
                    provider_id = await self._send_once(channel, payload, ticket_id)
        except Exception as e:
            error = sanitize_error_message(str(e)) or type(e).__name__
            await self._finish(key, "failed", attempts, error=error)
            await self._dead_letter(message, error, attempts)
            logger.error(
                "Outbound delivery failed",
                idempotency_key=key,
                channel=channel,
                attempts=attempts,
                error=error,
            )
            return "failed"

        await self._finish(key, "sent", attempts, provider_message_id=provider_id)
        logger.info(
            "Outbound reply delivered",
            idempotency_key=key,
            channel=channel,
            attempts=attempts,
        )
        return "sent"

    async def _escalate(self, ticket_id: UUID | None) -> None:
        if ticket_id is None:
            return
        async with self.db_pool.acquire() as conn:
            await conn.execute("UPDATE tickets SET status = 'escalated' WHERE id = $1", ticket_id)

    async def _dead_letter(self, message: KafkaMessage, error: str, attempts: int) -> None:
        try:
            await self.kafka_producer.send_message(
                DLQ_TOPIC,
                {
                    "original_topic": message.topic,
                    "original_message_id": message.message_id,
                    "original_payload": message.payload,
                    "error": error,
                    "attempts": attempts,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                key=message.payload.get("ticket_id"),
            )
        except Exception as e:
            # The failed row in outbound_deliveries is still the durable record.
            logger.critical(
                "DLQ publish failed for outbound reply",
                error=sanitize_error_message(str(e)),
            )


async def run_notification_sender_loop(
    db_pool: asyncpg.Pool,
    kafka_producer: Any,
    bootstrap_servers: str = "localhost:9092",
) -> None:
    """Consume notifications.outbound in its own consumer group."""
    consumer = KafkaConsumerClient(bootstrap_servers, group_id=CONSUMER_GROUP)
    sender = OutboundSender(db_pool, kafka_producer)

    await consumer.start([NOTIFICATIONS_OUTBOUND_TOPIC])
    try:
        await consumer.consume_messages(
            on_message=sender.handle, timeout_ms=1000, dlq_producer=kafka_producer
        )
    except asyncio.CancelledError:
        logger.info("Notification sender cancelled")
    finally:
        await consumer.stop()
