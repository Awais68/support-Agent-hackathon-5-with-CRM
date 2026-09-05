"""Gmail channel handler for TechFlow CRM Digital FTE."""

import os
import asyncio
from typing import Dict, Any
from email.mime.text import MIMEText
import base64

import structlog
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from exceptions import sanitize_error_message

from kafka_client import KafkaProducerClient, create_inbound_email_message
from utils.circuit_breaker import get_circuit_breaker, CircuitBreakerError

logger = structlog.get_logger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"]


class GmailHandler:
    """Handler for Gmail channel integration."""

    def __init__(self, kafka_producer: KafkaProducerClient):
        self.kafka_producer = kafka_producer
        self.service = None
        self.credentials_file = os.getenv("GMAIL_CREDENTIALS_FILE", "gmail_credentials.json")
        self.token_file = os.getenv("GMAIL_TOKEN_FILE", "gmail_token.json")

    async def authenticate(self) -> None:
        """Authenticate with Gmail API."""
        try:
            credentials = None

            # Load existing token
            if os.path.exists(self.token_file):
                credentials = Credentials.from_authorized_user_file(self.token_file, SCOPES)

            # Refresh token if expired. A revoked or expired refresh token
            # raises RefreshError ("invalid_grant") — that is recoverable by
            # re-running the OAuth flow below, so it must not abort startup.
            if credentials and credentials.expired and credentials.refresh_token:
                try:
                    credentials.refresh(Request())
                    with open(self.token_file, "w") as token:
                        token.write(credentials.to_json())
                except RefreshError as e:
                    logger.warning(
                        "Gmail refresh token rejected — re-running OAuth flow",
                        error=sanitize_error_message(str(e)),
                    )
                    credentials = None

            # Run the OAuth flow when there are no credentials, or when the ones
            # we have are still not usable after a refresh attempt.
            if not credentials or not credentials.valid:
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_file, SCOPES)
                credentials = flow.run_local_server(port=0)

                # Save credentials for future use
                with open(self.token_file, "w") as token:
                    token.write(credentials.to_json())

            self.service = build("gmail", "v1", credentials=credentials)
            logger.info("Gmail authentication successful")

        except Exception as e:
            logger.error("Gmail authentication failed", error=sanitize_error_message(str(e)))
            raise

    async def poll_inbox(self, poll_interval_seconds: int = 60) -> None:
        """Poll inbox for new messages."""
        if not self.service:
            await self.authenticate()

        try:
            _gmail_cb = get_circuit_breaker("gmail")
            async with _gmail_cb:
                results = self.service.users().messages().list(userId="me", q="is:unread").execute()
            messages = results.get("messages", [])

            if not messages:
                logger.info("No new messages")
                return

            logger.info("Found new messages", count=len(messages))

            for message in messages:
                await self._process_message(message["id"])

        except (HttpError, CircuitBreakerError) as error:
            logger.error("Gmail polling error", error=sanitize_error_message(str(error)))
            raise

    async def _process_message(self, message_id: str) -> None:
        """Process a single Gmail message."""
        try:
            _gmail_cb = get_circuit_breaker("gmail")
            async with _gmail_cb:
                message = self.service.users().messages().get(userId="me", id=message_id).execute()

            # Extract headers
            headers = {h["name"]: h["value"] for h in message["payload"]["headers"]}

            from_email = headers.get("From", "").split("<")[-1].rstrip(">")
            from_name = headers.get("From", "Unknown").split("<")[0].strip()
            subject = headers.get("Subject", "No Subject")

            # Extract body
            body_text = self._get_message_body(message)

            logger.info(
                "Processing Gmail message",
                message_id=message_id,
                from_email=from_email,
                subject=subject,
            )

            # Send to Kafka
            kafka_message = create_inbound_email_message(
                customer_email=from_email,
                sender_name=from_name,
                subject=subject,
                body=body_text,
            )

            await self.kafka_producer.send_message(
                kafka_message.topic,
                kafka_message.payload,
                key=from_email,
            )

            _gmail_modify_cb = get_circuit_breaker("gmail")
            async with _gmail_modify_cb:
                self.service.users().messages().modify(
                    userId="me",
                    id=message_id,
                    body={"removeLabelIds": ["UNREAD"]},
                ).execute()

            logger.info("Gmail message processed and marked as read", message_id=message_id)

        except Exception as e:
            logger.error("Error processing Gmail message", error=sanitize_error_message(str(e)), message_id=message_id)

    def _get_message_body(self, message: Dict[str, Any]) -> str:
        """Extract body text from Gmail message."""
        try:
            if "parts" in message["payload"]:
                for part in message["payload"]["parts"]:
                    if part["mimeType"] == "text/plain":
                        data = part.get("body", {}).get("data", "")
                        if data:
                            return base64.urlsafe_b64decode(data).decode("utf-8")
            else:
                data = message["payload"].get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8")
        except Exception as e:
            logger.error("Error extracting message body", error=sanitize_error_message(str(e)))

        return "Unable to extract message body"

    async def send_reply(self, to_email: str, subject: str, body: str) -> None:
        """Send a reply via Gmail."""
        if not self.service:
            await self.authenticate()

        try:
            message = MIMEText(body)
            message["To"] = to_email
            message["Subject"] = f"Re: {subject}"

            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

            _gmail_send_cb = get_circuit_breaker("gmail")
            async with _gmail_send_cb:
                self.service.users().messages().send(
                    userId="me",
                    body={"raw": raw_message},
                ).execute()

            logger.info("Reply sent", to_email=to_email, subject=subject)

        except (HttpError, CircuitBreakerError) as error:
            logger.error("Error sending Gmail reply", error=sanitize_error_message(str(error)))
            raise

    def extract_customer_info(self, message: Dict[str, Any]) -> Dict[str, str]:
        """Extract customer information from Gmail message."""
        try:
            headers = {h["name"]: h["value"] for h in message["payload"]["headers"]}

            return {
                "email": headers.get("From", "").split("<")[-1].rstrip(">"),
                "name": headers.get("From", "Unknown").split("<")[0].strip(),
                "subject": headers.get("Subject", ""),
                "timestamp": headers.get("Date", ""),
            }
        except Exception as e:
            logger.error("Error extracting customer info", error=sanitize_error_message(str(e)))
            return {}


async def run_gmail_polling_loop(
    kafka_producer: KafkaProducerClient,
    poll_interval_seconds: int = 60,
) -> None:
    """Run Gmail polling loop continuously."""
    handler = GmailHandler(kafka_producer)
    try:
        await handler.authenticate()
    except Exception as e:
        logger.warning("Gmail authentication failed, polling disabled", error=sanitize_error_message(str(e)))
        return

    while True:
        try:
            await handler.poll_inbox(poll_interval_seconds)
        except Exception as e:
            logger.error("Gmail polling loop error", error=sanitize_error_message(str(e)))

        await asyncio.sleep(poll_interval_seconds)
