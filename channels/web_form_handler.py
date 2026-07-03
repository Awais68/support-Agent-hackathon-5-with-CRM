"""Web form channel handler for TechFlow CRM Digital FTE."""

from typing import Optional, Dict, Any
from pydantic import BaseModel, EmailStr, validator
from pydantic import ValidationError as PydanticValidationError
from exceptions import sanitize_error_message
import structlog

from kafka_client import KafkaProducerClient, create_inbound_webform_message

logger = structlog.get_logger(__name__)


class WebFormSubmission(BaseModel):
    """Web form submission model."""

    name: str
    email: EmailStr
    subject: str
    message: str
    category: str = "general"
    priority: str = "medium"
    company: Optional[str] = None
    phone: Optional[str] = None

    @validator("name")
    def validate_name(cls, v):
        if not v or len(v) < 2:
            raise ValueError("Name must be at least 2 characters")
        return v.strip()

    @validator("subject")
    def validate_subject(cls, v):
        if not v or len(v) < 5:
            raise ValueError("Subject must be at least 5 characters")
        return v.strip()

    @validator("message")
    def validate_message(cls, v):
        if not v or len(v) < 10:
            raise ValueError("Message must be at least 10 characters")
        if len(v) > 1000:
            raise ValueError("Message cannot exceed 1000 characters")
        return v.strip()

    @validator("category")
    def validate_category(cls, v):
        valid_categories = ["general", "technical", "billing", "onboarding", "bug", "feedback"]
        if v not in valid_categories:
            raise ValueError(f"Category must be one of {valid_categories}")
        return v

    @validator("priority")
    def validate_priority(cls, v):
        valid_priorities = ["low", "medium", "high", "urgent", "critical"]
        if v not in valid_priorities:
            raise ValueError(f"Priority must be one of {valid_priorities}")
        return v


class WebFormHandler:
    """Handler for web form channel integration."""

    def __init__(self, kafka_producer: KafkaProducerClient):
        self.kafka_producer = kafka_producer

    async def validate_submission(self, data: Dict[str, Any]) -> tuple[bool, Optional[WebFormSubmission], Optional[str]]:
        """Validate a web form submission."""
        try:
            submission = WebFormSubmission(**data)
            return True, submission, None
        except (PydanticValidationError, ValueError, Exception) as e:
            logger.warning("Web form validation error", error=sanitize_error_message(str(e)))
            return False, None, sanitize_error_message(str(e))

    async def process_submission(
        self, submission: WebFormSubmission
    ) -> Dict[str, Any]:
        """Process a web form submission."""
        try:
            logger.info(
                "Processing web form submission",
                email=submission.email,
                subject=submission.subject,
                category=submission.category,
            )

            # Create Kafka message
            kafka_message = create_inbound_webform_message(
                customer_email=submission.email,
                customer_name=submission.name,
                subject=submission.subject,
                message_body=submission.message,
                category=submission.category,
                priority=submission.priority,
                customer_phone=submission.phone,
            )

            # Send to Kafka
            message_id = await self.kafka_producer.send_message(
                kafka_message.topic,
                kafka_message.payload,
                key=submission.email,
            )

            logger.info(
                "Web form submission routed to Kafka",
                email=submission.email,
                message_id=message_id,
            )

            return {
                "success": True,
                "message_id": message_id,
                "email": submission.email,
                "subject": submission.subject,
            }

        except Exception as e:
            logger.error(
                "Error processing web form submission",
                error=sanitize_error_message(str(e)),
                email=submission.email,
            )
            raise

    async def handle_attachment(
        self, email: str, filename: str, file_data: bytes
    ) -> Optional[str]:
        """Handle file attachment from web form.

        In production, this would upload to cloud storage (S3, GCS, etc.)
        and return a URL. For now, returns None.
        """
        try:
            logger.info(
                "Web form attachment received",
                email=email,
                filename=filename,
                size_bytes=len(file_data),
            )

            # TODO: Implement file upload to cloud storage
            # For now, log and skip

            return None

        except Exception as e:
            logger.error(
                "Error handling web form attachment",
                error=sanitize_error_message(str(e)),
                email=email,
                filename=filename,
            )
            return None

    def get_form_metadata(self) -> Dict[str, Any]:
        """Get metadata about the form for frontend."""
        return {
            "categories": ["general", "technical", "billing", "onboarding"],
            "priorities": ["low", "medium", "high", "critical"],
            "max_message_length": 1000,
            "allow_attachments": True,
            "estimated_response_times": {
                "starter": "24 hours",
                "growth": "8 hours",
                "enterprise": "2 hours",
            },
        }
