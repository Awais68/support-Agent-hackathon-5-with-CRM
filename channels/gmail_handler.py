"""Gmail channel handler for TechFlow CRM Digital FTE."""

import os
import json
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List
from email.mime.text import MIMEText
import base64

import structlog
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from kafka_client import KafkaProducerClient, create_inbound_email_message

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

            # Refresh token if expired
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())

            # If no credentials, run OAuth flow
            if not credentials:
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_file, SCOPES)
                credentials = flow.run_local_server(port=0)

                # Save credentials for future use
                with open(self.token_file, "w") as token:
                    token.write(credentials.to_json())

            self.service = build("gmail", "v1", credentials=credentials)
            logger.info("Gmail authentication successful")

        except Exception as e:
            logger.error("Gmail authentication failed", error=str(e))
            raise

    async def poll_inbox(self, poll_interval_seconds: int = 60) -> None:
        """Poll inbox for new messages."""
        if not self.service:
            await self.authenticate()

        try:
            # Query for unread messages
            results = self.service.users().messages().list(userId="me", q="is:unread").execute()
            messages = results.get("messages", [])

            if not messages:
                logger.info("No new messages")
                return

            logger.info("Found new messages", count=len(messages))

            for message in messages:
                await self._process_message(message["id"])

        except HttpError as error:
            logger.error("Gmail polling error", error=str(error))
            raise

    async def _process_message(self, message_id: str) -> None:
        """Process a single Gmail message."""
        try:
            # Get full message
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

            # Mark as read
            self.service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()

            logger.info("Gmail message processed and marked as read", message_id=message_id)

        except Exception as e:
            logger.error("Error processing Gmail message", error=str(e), message_id=message_id)

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
            logger.error("Error extracting message body", error=str(e))

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

            self.service.users().messages().send(
                userId="me",
                body={"raw": raw_message},
            ).execute()

            logger.info("Reply sent", to_email=to_email, subject=subject)

        except HttpError as error:
            logger.error("Error sending Gmail reply", error=str(error))
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
            logger.error("Error extracting customer info", error=str(e))
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
        logger.warning("Gmail authentication failed, polling disabled", error=str(e))
        return

    while True:
        try:
            await handler.poll_inbox(poll_interval_seconds)
        except Exception as e:
            logger.error("Gmail polling loop error", error=str(e))

        await asyncio.sleep(poll_interval_seconds)
