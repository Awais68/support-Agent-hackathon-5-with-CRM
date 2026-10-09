"""Kafka client for TechFlow CRM Digital FTE with DLQ routing and retry logic."""

import asyncio
import inspect
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from exceptions import sanitize_error_message
from utils import heartbeat
from utils.circuit_breaker import CircuitBreakerError, get_circuit_breaker

logger = structlog.get_logger(__name__)

# Topic definitions
INBOUND_EMAIL_TOPIC = "inbound.email"
INBOUND_WHATSAPP_TOPIC = "inbound.whatsapp"
INBOUND_WEBFORM_TOPIC = "inbound.webform"
INBOUND_VOICE_TOPIC = "inbound.voice"
AGENT_PROCESSING_TOPIC = "agent.processing"
AGENT_COMPLETED_TOPIC = "agent.completed"
NOTIFICATIONS_OUTBOUND_TOPIC = "notifications.outbound"
ESCALATIONS_TOPIC = "escalations"
METRICS_EVENTS_TOPIC = "metrics.events"
DLQ_TOPIC = "dlq"

ALL_TOPICS = [
    INBOUND_EMAIL_TOPIC,
    INBOUND_WHATSAPP_TOPIC,
    INBOUND_WEBFORM_TOPIC,
    INBOUND_VOICE_TOPIC,
    AGENT_PROCESSING_TOPIC,
    AGENT_COMPLETED_TOPIC,
    NOTIFICATIONS_OUTBOUND_TOPIC,
    ESCALATIONS_TOPIC,
    METRICS_EVENTS_TOPIC,
    DLQ_TOPIC,
]


class JSONEncoder(json.JSONEncoder):
    """Custom JSON encoder for UUID and datetime objects."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class KafkaMessage:
    """Envelope for Kafka messages with metadata."""

    def __init__(
        self,
        topic: str,
        payload: dict[str, Any],
        message_id: str | None = None,
        timestamp: datetime | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.topic = topic
        self.payload = payload
        # uuid4, not a timestamp-derived UUID: two messages produced within the
        # same microsecond would otherwise share an id.
        self.message_id = message_id or str(uuid4())
        self.timestamp = timestamp or datetime.now(UTC)
        self.headers = headers if headers is not None else {}

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "message_id": self.message_id,
            "topic": self.topic,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
            "headers": self.headers,
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), cls=JSONEncoder)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KafkaMessage":
        """Create from dictionary."""
        return cls(
            topic=data["topic"],
            payload=data["payload"],
            message_id=data.get("message_id"),
            timestamp=(
                datetime.fromisoformat(data["timestamp"])
                if isinstance(data.get("timestamp"), str)
                else data.get("timestamp")
            ),
            headers=data.get("headers", {}),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "KafkaMessage":
        """Create from JSON string."""
        return cls.from_dict(json.loads(json_str))


class KafkaProducerClient:
    """Async Kafka producer with DLQ routing and retry logic."""

    def __init__(self, bootstrap_servers: str):
        self.bootstrap_servers = bootstrap_servers
        self.producer: AIOKafkaProducer | None = None
        self.retry_policy = AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        )

    def _sasl_config(self) -> dict:
        """Build SASL config dict from environment variables."""
        protocol = os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
        config = {"security_protocol": protocol}
        if protocol in ("SASL_PLAINTEXT", "SASL_SSL"):
            config.update(
                {
                    "sasl_mechanism": os.getenv("KAFKA_SASL_MECHANISM", "PLAIN"),
                    "sasl_plain_username": os.getenv("KAFKA_SASL_USERNAME", ""),
                    "sasl_plain_password": os.getenv("KAFKA_SASL_PASSWORD", ""),
                }
            )
        return config

    async def start(self) -> None:
        """Start the producer."""
        self.producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            value_serializer=lambda v: v.encode("utf-8"),
            compression_type="gzip",
            **self._sasl_config(),
        )
        await self.producer.start()
        logger.info("Kafka producer started", servers=self.bootstrap_servers)

    async def stop(self) -> None:
        """Stop the producer."""
        if self.producer:
            await self.producer.stop()
            logger.info("Kafka producer stopped")

    async def send_message(
        self, topic: str, payload: dict[str, Any], key: str | None = None
    ) -> str:
        """Send a message to a Kafka topic with retry logic."""
        message = KafkaMessage(topic, payload)

        try:
            async for attempt in self.retry_policy:
                with attempt:
                    if not self.producer:
                        raise RuntimeError("Producer not started")

                    _kafka_cb = get_circuit_breaker("kafka")
                    async with _kafka_cb:
                        await self.producer.send_and_wait(
                            topic,
                            value=message.to_json(),
                            key=key.encode("utf-8") if key else None,
                        )

            logger.info(
                "Message sent",
                topic=topic,
                message_id=message.message_id,
                payload_keys=list(payload.keys()),
            )
            return message.message_id

        except (ConnectionError, TimeoutError, CircuitBreakerError, Exception) as e:
            logger.error(
                "Failed to send message, routing to DLQ",
                topic=topic,
                error=sanitize_error_message(str(e)),
                payload_keys=list(payload.keys()),
            )
            # Route to DLQ
            dlq_payload = {
                "original_topic": topic,
                "original_payload": payload,
                "error": sanitize_error_message(str(e)),
                "timestamp": datetime.now(UTC).isoformat(),
            }
            try:
                if self.producer:
                    _dlq_cb = get_circuit_breaker("kafka")
                    async with _dlq_cb:
                        await self.producer.send_and_wait(
                            DLQ_TOPIC,
                            value=KafkaMessage(DLQ_TOPIC, dlq_payload).to_json(),
                            key=key.encode("utf-8") if key else None,
                        )
                logger.info(f"Message routed to DLQ: {message.message_id}")
            except Exception as dlq_error:
                logger.critical(
                    "DLQ routing failed",
                    original_error=sanitize_error_message(str(e)),
                    dlq_error=sanitize_error_message(str(dlq_error)),
                )
            raise


class NoOpKafkaProducer:
    """No-op Kafka producer for deployments without a broker.

    Mirrors ``KafkaProducerClient``'s async interface so callers (agent,
    handlers) don't need to branch on whether Kafka is available. Used when
    ``ENABLE_KAFKA=false`` or when the broker is unreachable at startup.
    """

    async def start(self) -> None:
        """No-op start."""

    async def stop(self) -> None:
        """No-op stop."""

    async def send_message(
        self, topic: str, payload: dict[str, Any], key: str | None = None
    ) -> str:
        """Return a fake message id without sending anything."""
        return "noop-" + str(UUID(int=int(datetime.now().timestamp() * 1000000)))


class NonRetryableError(Exception):
    """Retrying cannot help; the consumer dead-letters the message at once."""


class KafkaConsumerClient:
    """Async Kafka consumer with at-least-once delivery.

    Offsets are committed by hand, one message at a time, and only after the
    handler succeeded or the message was dead-lettered. A crash at any point
    before that leaves the offset uncommitted, so the message is delivered
    again; handlers must therefore be idempotent (see inbound_processing).
    """

    def __init__(self, bootstrap_servers: str, group_id: str):
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.consumer: AIOKafkaConsumer | None = None
        # Monotonic start of the handler call in flight; None while idle.
        self._handling_since: float | None = None

    def _sasl_config(self) -> dict:
        """Build SASL config dict from environment variables."""
        protocol = os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
        config = {"security_protocol": protocol}
        if protocol in ("SASL_PLAINTEXT", "SASL_SSL"):
            config.update(
                {
                    "sasl_mechanism": os.getenv("KAFKA_SASL_MECHANISM", "PLAIN"),
                    "sasl_plain_username": os.getenv("KAFKA_SASL_USERNAME", ""),
                    "sasl_plain_password": os.getenv("KAFKA_SASL_PASSWORD", ""),
                }
            )
        return config

    async def start(self, topics: list[str]) -> None:
        """Start the consumer."""
        self.consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            value_deserializer=lambda m: m.decode("utf-8"),
            auto_offset_reset="earliest",
            # Committing on fetch lost every message whose handler failed or
            # whose worker died mid-run (AUDIT N4).
            enable_auto_commit=False,
            **self._sasl_config(),
        )
        await self.consumer.start()
        logger.info(
            "Kafka consumer started",
            group_id=self.group_id,
            topics=topics,
            servers=self.bootstrap_servers,
        )

    async def stop(self) -> None:
        """Stop the consumer."""
        if self.consumer:
            await self.consumer.stop()
            logger.info("Kafka consumer stopped")

    async def consume_messages(
        self,
        on_message: Callable[[KafkaMessage], Any],
        timeout_ms: int = 1000,
        dlq_producer: Any = None,
        max_attempts: int | None = None,
        retry_backoff_seconds: float | None = None,
    ) -> None:
        """Consume messages, retrying the handler and dead-lettering failures.

        Each message is tried up to ``max_attempts`` times (env
        ``KAFKA_HANDLER_MAX_ATTEMPTS``, default 3) with exponential backoff.
        After the last failure it is published to the DLQ via
        ``dlq_producer``; only then is its offset committed. If the DLQ write
        itself fails, the error propagates without committing.
        """
        if not self.consumer:
            raise RuntimeError("Consumer not started")
        attempts_allowed = max_attempts or int(os.getenv("KAFKA_HANDLER_MAX_ATTEMPTS", "3"))
        backoff = (
            retry_backoff_seconds
            if retry_backoff_seconds is not None
            else float(os.getenv("KAFKA_HANDLER_RETRY_BACKOFF_SECONDS", "1"))
        )

        beats = asyncio.create_task(self._heartbeat_loop())
        try:
            async for raw_message in self.consumer:
                try:
                    message = KafkaMessage.from_json(raw_message.value)
                except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
                    logger.error(
                        "Failed to parse message",
                        error=str(e),
                        raw_value=str(raw_message.value)[:200],
                    )
                    await self._dead_letter(
                        dlq_producer,
                        raw_message,
                        {"raw_value": str(raw_message.value)[:10000]},
                        f"unparseable: {e}",
                        attempts=0,
                    )
                    await self._commit(raw_message)
                    continue

                logger.info(
                    "Message received",
                    topic=message.topic,
                    message_id=message.message_id,
                )
                self._handling_since = asyncio.get_running_loop().time()
                try:
                    error = await self._handle_with_retries(
                        on_message, message, attempts_allowed, backoff
                    )
                finally:
                    self._handling_since = None
                if error is not None:
                    attempts, exc = error
                    await self._dead_letter(
                        dlq_producer,
                        raw_message,
                        {"message_id": message.message_id, "payload": message.payload},
                        sanitize_error_message(str(exc)),
                        attempts=attempts,
                        original_topic=message.topic,
                    )
                await self._commit(raw_message)

        except asyncio.CancelledError:
            logger.info("Consumer cancelled")
        except Exception as e:
            logger.error("Consumer error", error=sanitize_error_message(str(e)))
            raise
        finally:
            beats.cancel()

    async def _heartbeat_loop(self) -> None:
        """Refresh this group's heartbeat while the loop is not stuck (N6).

        Runs beside the consume loop so an idle topic still beats; skips the
        beat once one handler call has run longer than WORKER_STALL_SECONDS,
        which lets the probe restart a hung worker.
        """
        loop = asyncio.get_running_loop()
        while True:
            started = self._handling_since
            if started is None or loop.time() - started < heartbeat.stall_seconds():
                try:
                    heartbeat.beat(self.group_id)
                except OSError as e:
                    logger.warning("Heartbeat write failed", error=str(e))
            await asyncio.sleep(heartbeat.interval_seconds())

    async def _handle_with_retries(
        self,
        on_message: Callable[[KafkaMessage], Any],
        message: KafkaMessage,
        attempts_allowed: int,
        backoff: float,
    ) -> tuple[int, Exception] | None:
        """Run the handler; return (attempts, last error) if it never succeeded."""
        for attempt in range(1, attempts_allowed + 1):
            try:
                result = on_message(message)
                if inspect.iscoroutine(result) or isinstance(result, asyncio.Task):
                    await result
                return None
            except asyncio.CancelledError:
                raise
            except Exception as e:
                final = isinstance(e, NonRetryableError) or attempt == attempts_allowed
                logger.error(
                    "Handler error",
                    error=sanitize_error_message(str(e)),
                    message_topic=message.topic,
                    message_id=message.message_id,
                    attempt=attempt,
                    will_retry=not final,
                )
                if final:
                    return attempt, e
                if backoff:
                    await asyncio.sleep(backoff * 2 ** (attempt - 1))
        return None

    async def _dead_letter(
        self,
        dlq_producer: Any,
        raw_message: Any,
        body: dict,
        error: str,
        attempts: int,
        original_topic: str | None = None,
    ) -> None:
        if dlq_producer is None:
            # Nowhere to park it: keep the offset so the message is not lost.
            raise RuntimeError("Handler failed and no DLQ producer is configured")
        await dlq_producer.send_message(
            DLQ_TOPIC,
            {
                "original_topic": original_topic or raw_message.topic,
                "kafka_topic": raw_message.topic,
                "partition": raw_message.partition,
                "offset": raw_message.offset,
                "consumer_group": self.group_id,
                "attempts": attempts,
                "error": error,
                "timestamp": datetime.now(UTC).isoformat(),
                **body,
            },
        )
        logger.warning(
            "Message dead-lettered",
            topic=raw_message.topic,
            offset=raw_message.offset,
            attempts=attempts,
        )

    async def _commit(self, raw_message: Any) -> None:
        tp = TopicPartition(raw_message.topic, raw_message.partition)
        await self.consumer.commit({tp: raw_message.offset + 1})


def create_inbound_email_message(
    customer_email: str,
    sender_name: str,
    subject: str,
    body: str,
    message_id: str | None = None,
) -> KafkaMessage:
    """Create an inbound email message."""
    return KafkaMessage(
        topic=INBOUND_EMAIL_TOPIC,
        payload={
            "customer_email": customer_email,
            "sender_name": sender_name,
            "subject": subject,
            "body": body,
            "timestamp": datetime.now(UTC).isoformat(),
        },
        message_id=message_id,
        headers={"content_type": "email"},
    )


def create_inbound_whatsapp_message(
    customer_phone: str,
    customer_name: str,
    message_body: str,
    media_url: str | None = None,
    message_id: str | None = None,
) -> KafkaMessage:
    """Create an inbound WhatsApp message."""
    return KafkaMessage(
        topic=INBOUND_WHATSAPP_TOPIC,
        payload={
            "customer_phone": customer_phone,
            "customer_name": customer_name,
            "message_body": message_body,
            "media_url": media_url,
            "timestamp": datetime.now(UTC).isoformat(),
        },
        message_id=message_id,
        headers={"content_type": "whatsapp"},
    )


def create_inbound_webform_message(
    customer_email: str,
    customer_name: str,
    subject: str,
    message_body: str,
    category: str = "general",
    priority: str = "medium",
    customer_phone: str | None = None,
    message_id: str | None = None,
    ticket_id: str | None = None,
) -> KafkaMessage:
    """Create an inbound web form message.

    ``ticket_id`` carries a ticket the API already created so the consumer
    reuses it instead of creating a duplicate.
    """
    payload = {
        "customer_email": customer_email,
        "customer_name": customer_name,
        "subject": subject,
        "message_body": message_body,
        "category": category,
        "priority": priority,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if customer_phone:
        payload["customer_phone"] = customer_phone
    if ticket_id:
        payload["ticket_id"] = str(ticket_id)

    return KafkaMessage(
        topic=INBOUND_WEBFORM_TOPIC,
        payload=payload,
        message_id=message_id,
        headers={"content_type": "webform"},
    )


def create_inbound_voice_message(
    customer_phone: str,
    customer_name: str,
    message_body: str,
    language: str = "en",
    confidence: float = 1.0,
    audio_url: str | None = None,
    message_id: str | None = None,
) -> KafkaMessage:
    """Create an inbound voice message (STT result)."""
    payload: dict[str, Any] = {
        "customer_phone": customer_phone,
        "customer_name": customer_name,
        "message_body": message_body,
        "language": language,
        "confidence": confidence,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if audio_url:
        payload["audio_url"] = audio_url

    return KafkaMessage(
        topic=INBOUND_VOICE_TOPIC,
        payload=payload,
        message_id=message_id,
        headers={"content_type": "voice"},
    )


def create_agent_processing_message(
    ticket_id: str,
    customer_id: str,
    input_message: str,
    channel: str,
    message_id: str | None = None,
) -> KafkaMessage:
    """Create an agent processing event."""
    return KafkaMessage(
        topic=AGENT_PROCESSING_TOPIC,
        payload={
            "ticket_id": ticket_id,
            "customer_id": customer_id,
            "input_message": input_message,
            "channel": channel,
            "timestamp": datetime.now(UTC).isoformat(),
        },
        message_id=message_id,
        headers={"event_type": "agent_processing"},
    )


def create_escalation_message(
    ticket_id: str,
    customer_id: str,
    reason: str,
    priority: str = "high",
    context: dict[str, Any] | None = None,
    message_id: str | None = None,
) -> KafkaMessage:
    """Create an escalation event."""
    return KafkaMessage(
        topic=ESCALATIONS_TOPIC,
        payload={
            "ticket_id": ticket_id,
            "customer_id": customer_id,
            "reason": reason,
            "priority": priority,
            "context": context or {},
            "timestamp": datetime.now(UTC).isoformat(),
        },
        message_id=message_id,
        headers={"event_type": "escalation"},
    )
