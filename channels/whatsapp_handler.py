"""WhatsApp channel handler for TechFlow CRM Digital FTE using Twilio."""

import os
from typing import Optional, Dict, Any

import asyncio
import structlog
from twilio.request_validator import RequestValidator
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

    @property
    def signature_validation_enabled(self) -> bool:
        """Signature checking is only possible when an auth token is configured."""
        return bool(self.auth_token)

    def _webhook_url(self) -> str:
        """Absolute URL Twilio signed, as configured for this deployment."""
        url = self.webhook_url
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return f"https://{url}"

    def validate_webhook(
        self,
        signature: str,
        params: Optional[Dict[str, Any]] = None,
        url: Optional[str] = None,
    ) -> bool:
        """Validate a Twilio webhook signature.

        Uses Twilio's official RequestValidator: for form-encoded POSTs the
        signature is computed over the URL plus the sorted form parameters, not
        over the raw request body.
        """
        if not self.signature_validation_enabled:
            logger.warning("Cannot validate Twilio signature: TWILIO_AUTH_TOKEN not configured")
            return False
        if not signature:
            return False

        try:
            validator = RequestValidator(self.auth_token)
            is_valid = validator.validate(url or self._webhook_url(), params or {}, signature)
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
            # Phone number is the primary identifier on this channel; the
            # consumer resolves it to a customer via customer_identifiers.
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
