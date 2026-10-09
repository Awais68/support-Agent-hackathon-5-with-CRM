"""Channel handlers for TechFlow CRM Digital FTE."""

from channels.gmail_handler import GmailHandler
from channels.web_form_handler import WebFormHandler
from channels.whatsapp_handler import WhatsAppHandler

__all__ = ["GmailHandler", "WhatsAppHandler", "WebFormHandler"]
