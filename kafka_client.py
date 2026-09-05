"""Kafka client for TechFlow CRM Digital FTE with DLQ routing and retry logic."""

import json
import os
from datetime import UTC, datetime
from typing import Any, Callable, Dict, Optional
from uuid import UUID, uuid4
import asyncio
import inspect

from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
import structlog
from tenacity import (
    AsyncRetrying,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from exceptions import sanitize_error_message
from utils.circuit_breaker import get_circuit_breaker, CircuitBreakerError

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
        payload: Dict[str, Any],
        message_id: Optional[str] = None,
        timestamp: Optional[datetime] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.topic = topic
        self.payload = payload
        # uuid4, not a timestamp-derived UUID: two messages produced within the
        # same microsecond would otherwise share an id.
        self.message_id = message_id or str(uuid4())
        self.timestamp = timestamp or datetime.now(UTC)
        self.headers = headers if headers is not None else {}

    def to_dict(self) -> Dict[str, Any]:
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
    def from_dict(cls, data: Dict[str, Any]) -> "KafkaMessage":
        """Create from dictionary."""
        return cls(
            topic=data["topic"],
            payload=data["payload"],
            message_id=data.get("message_id"),
            timestamp=datetime.fromisoformat(data["timestamp"])
            if isinstance(data.get("timestamp"), str)
            else data.get("timestamp"),
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
        self.producer: Optional[AIOKafkaProducer] = None
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
            config.update({
                "sasl_mechanism": os.getenv("KAFKA_SASL_MECHANISM", "PLAIN"),
                "sasl_plain_username": os.getenv("KAFKA_SASL_USERNAME", ""),
                "sasl_plain_password": os.getenv("KAFKA_SASL_PASSWORD", ""),
            })
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
        self, topic: str, payload: Dict[str, Any], key: Optional[str] = None
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


class KafkaConsumerClient:
    """Async Kafka consumer with error handling."""

    def __init__(self, bootstrap_servers: str, group_id: str):
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.consumer: Optional[AIOKafkaConsumer] = None

    def _sasl_config(self) -> dict:
        """Build SASL config dict from environment variables."""
        protocol = os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
        config = {"security_protocol": protocol}
        if protocol in ("SASL_PLAINTEXT", "SASL_SSL"):
            config.update({
                "sasl_mechanism": os.getenv("KAFKA_SASL_MECHANISM", "PLAIN"),
                "sasl_plain_username": os.getenv("KAFKA_SASL_USERNAME", ""),
                "sasl_plain_password": os.getenv("KAFKA_SASL_PASSWORD", ""),
            })
        return config

    async def start(self, topics: list[str]) -> None:
        """Start the consumer."""
        self.consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            value_deserializer=lambda m: m.decode("utf-8"),
            auto_offset_reset="earliest",
            enable_auto_commit=True,
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
    ) -> None:
        """Consume messages from subscribed topics."""
        if not self.consumer:
            raise RuntimeError("Consumer not started")

        try:
            async for raw_message in self.consumer:
                message = None
                try:
                    message = KafkaMessage.from_json(raw_message.value)
                    logger.info(
                        "Message received",
                        topic=message.topic,
                        message_id=message.message_id,
                    )

                    # Call handler
                    result = on_message(message)
                    if inspect.iscoroutine(result) or isinstance(result, asyncio.Task):
                        await result

                except json.JSONDecodeError as e:
                    logger.error(
                        "Failed to parse message",
                        error=str(e),
                        raw_value=raw_message.value[:200],
                    )
                except Exception as e:
                    logger.error(
                        "Handler error",
                        error=sanitize_error_message(str(e)),
                        message_topic=getattr(message, "topic", None) or "unknown",
                    )

        except asyncio.CancelledError:
            logger.info("Consumer cancelled")
        except Exception as e:
            logger.error("Consumer error", error=sanitize_error_message(str(e)))
            raise


def create_inbound_email_message(
    customer_email: str,
    sender_name: str,
    subject: str,
    body: str,
    message_id: Optional[str] = None,
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
    media_url: Optional[str] = None,
    message_id: Optional[str] = None,
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
    customer_phone: Optional[str] = None,
    message_id: Optional[str] = None,
    ticket_id: Optional[str] = None,
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
    message_id: Optional[str] = None,
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
    context: Optional[Dict[str, Any]] = None,
    message_id: Optional[str] = None,
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
