"""Message processor worker for TechFlow CRM Digital FTE."""

import asyncio
import os
import signal
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import asyncpg
import structlog

from agent.customer_success_agent import (
    AgentContext,
    build_agent,
)
from channels.gmail_handler import run_gmail_polling_loop
from chat_provider import build_chat_client
from database import queries as db
from embeddings_provider import build_embedding_provider
from env_config import load_environment
from exceptions import sanitize_error_message
from kafka_client import (
    INBOUND_EMAIL_TOPIC,
    INBOUND_WEBFORM_TOPIC,
    INBOUND_WHATSAPP_TOPIC,
    KafkaConsumerClient,
    KafkaMessage,
    KafkaProducerClient,
    NonRetryableError,
    NoOpKafkaProducer,
)
from utils import heartbeat
from utils.redact import redact_dsn
from workers.metrics_collector import run_metrics_collector
from workers.notification_sender import run_notification_sender_loop

logger = structlog.get_logger(__name__)

# A message redelivered this many times (worker crash loop, poison input) is
# dead-lettered instead of being tried again.
MAX_DELIVERIES = int(os.getenv("INBOUND_MAX_DELIVERIES", "10"))


@dataclass
class _Delivery:
    """One delivery of an inbound message, as claimed in the ledger."""

    message_id: str
    attempt: int
    ticket_id: UUID | None
    claimed_at: datetime | None


class MessageProcessor:
    """Worker that processes inbound messages and routes to agent."""

    def __init__(self, db_pool: asyncpg.Pool, kafka_producer: KafkaProducerClient):
        self.db_pool = db_pool
        self.kafka_producer = kafka_producer
        # DeepSeek when DEEPSEEK_API_KEY is set, otherwise OpenRouter.
        self.openai_client = build_chat_client()
        # Embeddings go to their own provider: OpenRouter serves chat here but
        # has no embedding credits, so knowledge base search runs on Gemini.
        self.embedding_provider = build_embedding_provider(self.openai_client)

    async def process_message(self, message: KafkaMessage) -> None:
        """Process an inbound message; raise on failure.

        Delivery is at-least-once, so the message is first claimed in the
        inbound_processing ledger: a redelivery of a finished message is
        skipped, and one that died part-way resumes on the ticket it already
        created. Errors propagate so the consumer retries and finally
        dead-letters the message instead of dropping it (AUDIT N4).
        """
        topic = message.topic
        payload = message.payload
        claim = await db.claim_inbound_message(self.db_pool, message.message_id, topic)
        if claim is None:
            logger.info(
                "Inbound message already handled; skipping redelivery",
                topic=topic,
                message_id=message.message_id,
            )
            return
        delivery = _Delivery(
            message_id=message.message_id,
            attempt=claim["attempts"],
            ticket_id=claim["ticket_id"],
            claimed_at=claim["created_at"],
        )
        if delivery.attempt > MAX_DELIVERIES:
            reason = f"gave up after {delivery.attempt - 1} deliveries"
            await db.record_inbound_error(self.db_pool, message.message_id, reason, dead=True)
            raise NonRetryableError(reason)

        logger.info(
            "Processing message",
            topic=topic,
            message_id=message.message_id,
            attempt=delivery.attempt,
            payload_keys=list(payload.keys()),
        )
        try:
            agent_context = AgentContext(
                db_pool=self.db_pool,
                kafka_producer=self.kafka_producer,
                openai_client=self.openai_client,
                embedding_provider=self.embedding_provider,
                logger=logger,
            )
            agent = await build_agent(agent_context)

            if topic == INBOUND_EMAIL_TOPIC:
                await self._process_email_message(agent, payload, delivery)
            elif topic == INBOUND_WHATSAPP_TOPIC:
                await self._process_whatsapp_message(agent, payload, delivery)
            elif topic == INBOUND_WEBFORM_TOPIC:
                await self._process_webform_message(agent, payload, delivery)
            else:
                logger.warning("Unknown topic", topic=topic)
        except Exception as e:
            error = sanitize_error_message(str(e))
            logger.error(
                "Error processing message",
                error=error,
                topic=topic,
                message_id=message.message_id,
                attempt=delivery.attempt,
            )
            try:
                await db.record_inbound_error(self.db_pool, message.message_id, error)
            except Exception:
                logger.warning("Could not record inbound error", message_id=message.message_id)
            raise

        await db.mark_inbound_done(self.db_pool, message.message_id)

    async def _ticket_for(self, delivery: "_Delivery", create) -> dict:
        """The ticket an earlier delivery created, or a new one via ``create``."""
        if delivery.ticket_id:
            ticket = await db.get_ticket(self.db_pool, delivery.ticket_id)
            if ticket:
                return ticket
        ticket = await create()
        await db.set_inbound_ticket(self.db_pool, delivery.message_id, ticket["id"])
        return ticket

    async def _answer(self, agent, delivery: "_Delivery", ticket: dict, **kwargs) -> None:
        """Run the agent unless an earlier delivery already answered."""
        if (
            delivery.attempt > 1
            and delivery.claimed_at is not None
            and await db.has_completed_agent_run_since(
                self.db_pool, ticket["id"], delivery.claimed_at
            )
        ):
            logger.info(
                "Agent already answered this message; not running it again",
                ticket_number=ticket["ticket_number"],
                message_id=delivery.message_id,
            )
            return
        await agent.process_customer_message(
            ticket_id=ticket["id"],
            ticket_number=ticket["ticket_number"],
            source_message_id=delivery.message_id,
            **kwargs,
        )

    async def _process_email_message(self, agent, payload: dict, delivery: "_Delivery") -> None:
        """Process an email message."""
        try:
            customer_email = payload.get("customer_email", "")
            sender_name = payload.get("sender_name", "")
            subject = payload.get("subject", "")
            body = payload.get("body", "")

            logger.info(
                "Processing email",
                from_email=customer_email,
                subject=subject,
            )

            customer = await db.get_customer_or_create_by_identifier(
                self.db_pool,
                identifier_type="email",
                identifier_value=customer_email,
                name=sender_name,
            )

            ticket = await self._ticket_for(
                delivery,
                lambda: db.create_ticket(
                    self.db_pool,
                    customer_email=customer_email,
                    customer_id=customer["id"],
                    subject=subject,
                    category="general",
                    priority="medium",
                    channel="email",
                    initial_message=body,
                ),
            )

            await self._answer(
                agent,
                delivery,
                ticket,
                customer_id=customer["id"],
                customer_email=customer_email,
                customer_name=sender_name,
                message=body,
                channel="email",
            )

            logger.info("Email processed successfully", ticket_number=ticket["ticket_number"])

        except (asyncpg.PostgresError, Exception) as e:
            logger.error("Error processing email message", error=sanitize_error_message(str(e)))
            raise

    async def _process_whatsapp_message(self, agent, payload: dict, delivery: "_Delivery") -> None:
        """Process a WhatsApp message."""
        try:
            customer_phone = payload.get("customer_phone", "")
            customer_name = payload.get("customer_name", "")
            message_body = payload.get("message_body", "")

            logger.info(
                "Processing WhatsApp",
                customer_phone=customer_phone,
                customer_name=customer_name,
            )

            customer = await db.get_customer_or_create_by_identifier(
                self.db_pool,
                identifier_type="phone",
                identifier_value=customer_phone,
                name=customer_name,
            )
            customer_email = customer["email"]

            ticket = await self._ticket_for(
                delivery,
                lambda: db.create_ticket(
                    self.db_pool,
                    customer_email=customer_email,
                    customer_id=customer["id"],
                    subject=f"WhatsApp: {message_body[:50]}",
                    category="general",
                    priority="medium",
                    channel="whatsapp",
                    initial_message=message_body,
                ),
            )

            await self._answer(
                agent,
                delivery,
                ticket,
                customer_id=customer["id"],
                customer_email=customer_email,
                customer_name=customer_name,
                message=message_body,
                channel="whatsapp",
            )

            logger.info(
                "WhatsApp message processed successfully", ticket_number=ticket["ticket_number"]
            )

        except (asyncpg.PostgresError, Exception) as e:
            logger.error("Error processing WhatsApp message", error=sanitize_error_message(str(e)))
            raise

    async def _process_webform_message(self, agent, payload: dict, delivery: "_Delivery") -> None:
        """Process a web form message."""
        try:
            customer_email = payload.get("customer_email", "")
            customer_name = payload.get("customer_name", "")
            subject = payload.get("subject", "")
            message_body = payload.get("message_body", "")
            category = payload.get("category", "general")
            priority = payload.get("priority", "medium")
            customer_phone = payload.get("customer_phone")

            logger.info(
                "Processing web form",
                email=customer_email,
                subject=subject,
                phone=customer_phone,
            )

            customer = await db.get_customer_or_create_by_identifier(
                self.db_pool,
                identifier_type="email",
                identifier_value=customer_email,
                name=customer_name,
            )

            if customer_phone:
                await db.link_identifiers(
                    self.db_pool,
                    customer_id=customer["id"],
                    identifiers=[("phone", customer_phone)],
                )

            # The API already created the ticket when it accepted the submission;
            # reuse it so the customer does not end up with two tickets.
            existing_ticket_id = payload.get("ticket_id")
            if existing_ticket_id and not delivery.ticket_id:
                if await db.get_ticket(self.db_pool, UUID(str(existing_ticket_id))):
                    delivery.ticket_id = UUID(str(existing_ticket_id))
                else:
                    logger.warning(
                        "Webform payload referenced an unknown ticket; creating a new one",
                        ticket_id=str(existing_ticket_id),
                    )

            ticket = await self._ticket_for(
                delivery,
                lambda: db.create_ticket(
                    self.db_pool,
                    customer_email=customer_email,
                    customer_id=customer["id"],
                    subject=subject,
                    category=category,
                    priority=priority,
                    channel="webform",
                    initial_message=message_body,
                ),
            )

            await self._answer(
                agent,
                delivery,
                ticket,
                customer_id=customer["id"],
                customer_email=customer_email,
                customer_name=customer_name,
                message=message_body,
                channel="webform",
            )

            logger.info("Web form processed successfully", ticket_number=ticket["ticket_number"])

        except (asyncpg.PostgresError, Exception) as e:
            logger.error("Error processing web form message", error=sanitize_error_message(str(e)))
            raise


async def run_kafka_consumer_loop(
    db_pool: asyncpg.Pool,
    kafka_producer: KafkaProducerClient,
    bootstrap_servers: str = "localhost:9092",
) -> None:
    """Run Kafka consumer loop for processing messages."""
    consumer = KafkaConsumerClient(bootstrap_servers, group_id="techflow-message-processor")
    processor = MessageProcessor(db_pool, kafka_producer)

    topics = [
        INBOUND_EMAIL_TOPIC,
        INBOUND_WHATSAPP_TOPIC,
        INBOUND_WEBFORM_TOPIC,
    ]

    await consumer.start(topics)

    try:
        await consumer.consume_messages(
            on_message=processor.process_message,
            timeout_ms=1000,
            dlq_producer=kafka_producer,
        )
    except asyncio.CancelledError:
        logger.info("Consumer cancelled")
    finally:
        await consumer.stop()


async def run_process_heartbeat() -> None:
    """Beat while the event loop runs, so a metrics-only worker has a probe too."""
    while True:
        try:
            heartbeat.beat("worker")
        except OSError as e:
            logger.warning("Heartbeat write failed", error=str(e))
        await asyncio.sleep(heartbeat.interval_seconds())


async def run_until_signalled(tasks: list) -> None:
    """Run the worker tasks until they finish or SIGTERM/SIGINT arrives.

    As PID 1 in a container the process has no default SIGTERM action, so
    without a handler `docker stop` and pod termination waited out the grace
    period and SIGKILLed the worker (AUDIT X3).
    """
    loop = asyncio.get_running_loop()
    runner = asyncio.gather(*tasks)
    signals = (signal.SIGTERM, signal.SIGINT)
    for sig in signals:
        loop.add_signal_handler(sig, runner.cancel)
    try:
        await runner
    except asyncio.CancelledError:
        logger.info("Worker shutting down on signal")
    finally:
        for sig in signals:
            loop.remove_signal_handler(sig)


async def main():
    """Main entry point for worker."""
    load_environment()
    # Probes read these files (python -m utils.worker_healthcheck); stale ones
    # from a previous run of this container must not count.
    heartbeat.reset()

    # Load configuration
    db_url = os.getenv("DATABASE_URL", "postgresql://localhost/techflow")
    kafka_bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

    logger.info(
        "Starting message processor worker",
        db_url=redact_dsn(db_url),
        kafka_bootstrap=kafka_bootstrap,
    )

    # Create database pool
    pool_min = int(os.getenv("DATABASE_POOL_MIN", "1"))
    pool_max = int(os.getenv("DATABASE_POOL_MAX", "5"))
    db_ssl = os.getenv("DATABASE_SSL", "disable")
    db_pool = await asyncpg.create_pool(
        db_url,
        min_size=pool_min,
        max_size=pool_max,
        ssl=db_ssl,
        init=db.init_pgvector_connection,
    )

    # Create Kafka producer (optional — degraded mode runs metrics only)
    enable_kafka = os.getenv("ENABLE_KAFKA", "true").lower() in ("1", "true", "yes")
    if enable_kafka:
        kafka_producer = KafkaProducerClient(kafka_bootstrap)
        await kafka_producer.start()
        tasks = [
            run_gmail_polling_loop(kafka_producer, poll_interval_seconds=60),
            run_kafka_consumer_loop(db_pool, kafka_producer, kafka_bootstrap),
            run_notification_sender_loop(db_pool, kafka_producer, kafka_bootstrap),
            run_metrics_collector(db_pool, kafka_producer, collection_interval=300),
            run_process_heartbeat(),
        ]
    else:
        logger.info("Kafka disabled — worker running metrics collection only")
        kafka_producer = NoOpKafkaProducer()
        tasks = [
            run_metrics_collector(db_pool, kafka_producer, collection_interval=300),
            run_process_heartbeat(),
        ]

    try:
        # Run Gmail polling, inbound consumer, outbound sender and metrics
        await run_until_signalled(tasks)
    finally:
        await kafka_producer.stop()
        await db_pool.close()
        logger.info("Worker shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
