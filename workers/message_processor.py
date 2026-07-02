"""Message processor worker for TechFlow CRM Digital FTE."""

import asyncio
import os
from typing import Optional

import asyncpg
import structlog
from openai import AsyncOpenAI

from channels.gmail_handler import run_gmail_polling_loop
from workers.metrics_collector import run_metrics_collector
from kafka_client import (
    KafkaProducerClient,
    KafkaConsumerClient,
    INBOUND_EMAIL_TOPIC,
    INBOUND_WHATSAPP_TOPIC,
    INBOUND_WEBFORM_TOPIC,
    KafkaMessage,
)
from agent.customer_success_agent import (
    build_agent,
    AgentContext,
)
from database import queries as db

logger = structlog.get_logger(__name__)


class MessageProcessor:
    """Worker that processes inbound messages and routes to agent."""

    def __init__(self, db_pool: asyncpg.Pool, kafka_producer: KafkaProducerClient):
        self.db_pool = db_pool
        self.kafka_producer = kafka_producer
        self.openai_client = AsyncOpenAI()

    async def process_message(self, message: KafkaMessage) -> None:
        """Process an inbound message."""
        try:
            topic = message.topic
            payload = message.payload

            logger.info(
                "Processing message",
                topic=topic,
                message_id=message.message_id,
                payload_keys=list(payload.keys()),
            )

            # Create agent context
            agent_context = AgentContext(
                db_pool=self.db_pool,
                kafka_producer=self.kafka_producer,
                openai_client=self.openai_client,
                logger=logger,
            )

            # Build agent
            agent = await build_agent(agent_context)

            # Route by topic
            if topic == INBOUND_EMAIL_TOPIC:
                await self._process_email_message(agent, payload)
            elif topic == INBOUND_WHATSAPP_TOPIC:
                await self._process_whatsapp_message(agent, payload)
            elif topic == INBOUND_WEBFORM_TOPIC:
                await self._process_webform_message(agent, payload)
            else:
                logger.warning("Unknown topic", topic=topic)

        except Exception as e:
            logger.error(
                "Error processing message",
                error=str(e),
                topic=message.topic,
                message_id=message.message_id,
            )
            # Message will be retried by consumer

    async def _process_email_message(self, agent, payload: dict) -> None:
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

            ticket = await db.create_ticket(
                self.db_pool,
                customer_email=customer_email,
                customer_id=customer["id"],
                subject=subject,
                category="general",
                priority="medium",
                channel="email",
                initial_message=body,
            )

            # Process with agent
            result = await agent.process_customer_message(
                ticket_id=ticket["id"],
                customer_id=customer["id"],
                customer_email=customer_email,
                customer_name=sender_name,
                message=body,
                channel="email",
            )

            logger.info("Email processed successfully", ticket_number=ticket["ticket_number"])

        except Exception as e:
            logger.error("Error processing email message", error=str(e))
            raise

    async def _process_whatsapp_message(self, agent, payload: dict) -> None:
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

            ticket = await db.create_ticket(
                self.db_pool,
                customer_email=customer_email,
                customer_id=customer["id"],
                subject=f"WhatsApp: {message_body[:50]}",
                category="general",
                priority="medium",
                channel="whatsapp",
                initial_message=message_body,
            )

            # Process with agent
            result = await agent.process_customer_message(
                ticket_id=ticket["id"],
                customer_id=customer["id"],
                customer_email=customer_email,
                customer_name=customer_name,
                message=message_body,
                channel="whatsapp",
            )

            logger.info("WhatsApp message processed successfully", ticket_number=ticket["ticket_number"])

        except Exception as e:
            logger.error("Error processing WhatsApp message", error=str(e))
            raise

    async def _process_webform_message(self, agent, payload: dict) -> None:
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

            ticket = await db.create_ticket(
                self.db_pool,
                customer_email=customer_email,
                customer_id=customer["id"],
                subject=subject,
                category=category,
                priority=priority,
                channel="webform",
                initial_message=message_body,
            )

            # Process with agent
            result = await agent.process_customer_message(
                ticket_id=ticket["id"],
                customer_id=customer["id"],
                customer_email=customer_email,
                customer_name=customer_name,
                message=message_body,
                channel="webform",
            )

            logger.info("Web form processed successfully", ticket_number=ticket["ticket_number"])

        except Exception as e:
            logger.error("Error processing web form message", error=str(e))
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
        )
    except asyncio.CancelledError:
        logger.info("Consumer cancelled")
    finally:
        await consumer.stop()


async def main():
    """Main entry point for worker."""
    # Load configuration
    db_url = os.getenv("DATABASE_URL", "postgresql://localhost/techflow")
    kafka_bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

    logger.info(
        "Starting message processor worker",
        db_url=db_url,
        kafka_bootstrap=kafka_bootstrap,
    )

    # Create database pool
    db_pool = await asyncpg.create_pool(db_url)
    await db.register_pgvector_codec(db_pool)

    # Create Kafka producer
    kafka_producer = KafkaProducerClient(kafka_bootstrap)
    await kafka_producer.start()

    try:
        # Run Gmail polling, Kafka consumer, and metrics collector
        await asyncio.gather(
            run_gmail_polling_loop(kafka_producer, poll_interval_seconds=60),
            run_kafka_consumer_loop(db_pool, kafka_producer, kafka_bootstrap),
            run_metrics_collector(db_pool, kafka_producer, collection_interval=300),
        )
    except KeyboardInterrupt:
        logger.info("Worker shutting down...")
    finally:
        await kafka_producer.stop()
        await db_pool.close()
        logger.info("Worker shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
