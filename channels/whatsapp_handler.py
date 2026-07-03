"""WhatsApp channel handler for TechFlow CRM Digital FTE using Twilio."""

import os
import base64
import hmac
import hashlib
from typing import Optional, Dict, Any

import asyncio
import structlog
from twilio.rest import Client
from exceptions import sanitize_error_message

from kafka_client import KafkaProducerClient, create_inbound_whatsapp_message
from utils.circuit_breaker import get_circuit_breaker, CircuitBreakerError

logger = structlog.get_logger(__name__)


class WhatsAppHandler:
    """Handler for WhatsApp channel integration via Twilio."""

    def __init__(self, kafka_producer: KafkaProducerClient):
        self.kafka_producer = kafka_producer
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        self.whatsapp_number = os.getenv("TWILIO_WHATSAPP_NUMBER")
        self.webhook_url = os.getenv("TWILIO_WEBHOOK_URL", "http://localhost:8000/webhooks/whatsapp")

        # Soft-fail: log warning instead of crashing if credentials are missing
        self._configured = all([self.account_sid, self.auth_token, self.whatsapp_number])
        if not self._configured:
            logger.warning("WhatsApp running in degraded mode: Twilio credentials not configured")
            self.client = None
        else:
            self.client = Client(self.account_sid, self.auth_token)

    def validate_webhook(self, request_body: str, signature: str) -> bool:
        """Validate Twilio webhook signature."""
        try:
            # Reconstruct the Twilio request URL
            url = self.webhook_url
            if self.webhook_url.startswith("http://"):
                url = self.webhook_url
            else:
                # In production, use HTTPS
                url = f"https://{self.webhook_url}"

            # Compute the hash
            expected_hash = hmac.new(
                self.auth_token.encode(),
                (url + request_body).encode(),
                hashlib.sha1,
            ).digest()

            expected_signature = f"twilio {base64.b64encode(expected_hash).decode()}"

            # Normalize and compare
            is_valid = hmac.compare_digest(
                expected_signature.lower(),
                signature.lower(),
            )

            if not is_valid:
                logger.warning("Invalid Twilio webhook signature", signature=signature[:20])

            return is_valid

        except Exception as e:
            logger.error("Webhook validation error", error=sanitize_error_message(str(e)))
            return False

    async def parse_webhook(self, form_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse incoming WhatsApp webhook from Twilio."""
        try:
            # Twilio sends form-encoded data
            from_number = form_data.get("From", "").replace("whatsapp:", "")
            message_body = form_data.get("Body", "")
            message_sid = form_data.get("MessageSid", "")
            account_sid = form_data.get("AccountSid", "")
            num_media = int(form_data.get("NumMedia", 0))

            # Get sender name (not available from Twilio, use phone)
            sender_name = f"Customer {from_number[-4:]}"

            if not message_body and num_media == 0:
                logger.warning("Empty WhatsApp message", message_sid=message_sid)
                return None

            # Handle media if present
            media_url = None
            if num_media > 0:
                media_url = form_data.get("MediaUrl0")

            logger.info(
                "WhatsApp message parsed",
                from_number=from_number,
                message_sid=message_sid,
                has_media=num_media > 0,
            )

            return {
                "from_number": from_number,
                "sender_name": sender_name,
                "message_body": message_body,
                "message_sid": message_sid,
                "media_url": media_url,
                "account_sid": account_sid,
            }

        except Exception as e:
            logger.error("Error parsing WhatsApp webhook", error=sanitize_error_message(str(e)))
            return None

    async def handle_incoming_message(
        self, from_number: str, sender_name: str, message_body: str, media_url: Optional[str] = None
    ) -> None:
        """Handle incoming WhatsApp message."""
        try:
            # For the CRM, we use phone number as primary identifier
            # In production, map to customer email via a lookup table
            customer_identifier = from_number

            # Send to Kafka
            kafka_message = create_inbound_whatsapp_message(
                customer_phone=from_number,
                customer_name=sender_name,
                message_body=message_body,
                media_url=media_url,
            )

            await self.kafka_producer.send_message(
                kafka_message.topic,
                kafka_message.payload,
                key=from_number,
            )

            logger.info(
                "WhatsApp message routed to Kafka",
                from_number=from_number,
                sender_name=sender_name,
            )

        except Exception as e:
            logger.error(
                "Error handling WhatsApp message",
                error=sanitize_error_message(str(e)),
                from_number=from_number,
            )

    async def send_message(
        self, to_number: str, message_body: str, media_url: Optional[str] = None
    ) -> str:
        """Send a message via WhatsApp."""
        if not self._configured:
            logger.warning("WhatsApp not configured, message not sent", to_number=to_number)
            return "not-configured"

        try:
            # Enforce 300 character limit for WhatsApp (per spec)
            if len(message_body) > 300:
                logger.warning(
                    "Message exceeds WhatsApp limit",
                    original_length=len(message_body),
                    truncated_length=300,
                )
                message_body = message_body[:297] + "..."

            # Format phone number
            if not to_number.startswith("whatsapp:"):
                to_number = f"whatsapp:{to_number}"

            _twilio_cb = get_circuit_breaker("twilio")
            try:
                async with _twilio_cb:
                    loop = asyncio.get_event_loop()
                    message = await loop.run_in_executor(
                        None,
                        lambda: self.client.messages.create(
                            from_=f"whatsapp:{self.whatsapp_number}",
                            to=to_number,
                            body=message_body,
                            media_url=media_url,
                        ),
                    )
            except CircuitBreakerError:
                logger.warning(
                    "WhatsApp circuit open, message not sent",
                    to_number=to_number,
                )
                return "circuit-open"

            logger.info(
                "WhatsApp message sent",
                to_number=to_number,
                message_sid=message.sid,
                has_media=media_url is not None,
            )

            return message.sid

        except Exception as e:
            logger.error(
                "Error sending WhatsApp message",
                error=sanitize_error_message(str(e)),
                to_number=to_number,
            )
            raise

    async def send_template_message(
        self, to_number: str, template_sid: str, parameters: Optional[list] = None
    ) -> str:
        """Send a template message via WhatsApp."""
        if not self._configured:
            logger.warning("WhatsApp not configured, template message not sent", to_number=to_number)
            return "not-configured"

        try:
            if not to_number.startswith("whatsapp:"):
                to_number = f"whatsapp:{to_number}"

            _twilio_cb = get_circuit_breaker("twilio")
            try:
                async with _twilio_cb:
                    loop = asyncio.get_event_loop()
                    message = await loop.run_in_executor(
                        None,
                        lambda: self.client.messages.create(
                            from_=f"whatsapp:{self.whatsapp_number}",
                            to=to_number,
                            content_sid=template_sid,
                            content_variables=parameters or [],
                        ),
                    )
            except CircuitBreakerError:
                logger.warning(
                    "WhatsApp circuit open, template message not sent",
                    to_number=to_number,
                    template_sid=template_sid,
                )
                return "circuit-open"

            logger.info(
                "WhatsApp template message sent",
                to_number=to_number,
                template_sid=template_sid,
                message_sid=message.sid,
            )

            return message.sid

        except Exception as e:
            logger.error(
                "Error sending WhatsApp template message",
                error=sanitize_error_message(str(e)),
                to_number=to_number,
            )
            raise

    def get_webhook_url(self) -> str:
        """Get the webhook URL for Twilio configuration."""
        return self.webhook_url
