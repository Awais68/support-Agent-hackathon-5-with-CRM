"""Channel handlers for TechFlow CRM Digital FTE."""

from channels.gmail_handler import GmailHandler
from channels.whatsapp_handler import WhatsAppHandler
from channels.web_form_handler import WebFormHandler

__all__ = ["GmailHandler", "WhatsAppHandler", "WebFormHandler"]
