"""Response formatters for different channels."""

from typing import Optional, Dict, Any


def _word_count(text: str) -> int:
    """Count words in text."""
    return len(text.split())


def _truncate_by_words(text: str, max_words: int) -> str:
    """Truncate text to max_words."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


def truncate_for_channel(text: str, channel: str, max_chars: int = None) -> str:
    """Truncate and format text for a specific channel.

    Spec constraints:
    - WhatsApp: max 300 characters (concise)
    - Gmail: max 500 words (formal)
    - Web Form: max 300 words (semi-formal)
    """
    if channel == "whatsapp":
        max_chars = max_chars or 300
    elif channel == "email":
        return _truncate_by_words(text, 500)
    elif channel == "webform":
        return _truncate_by_words(text, 300)
    else:
        max_chars = max_chars or 3000

    if len(text) > max_chars:
        truncated = text[: max_chars - 3] + "..."
        return truncated
    return text


def format_email_response(
    body: str,
    customer_name: str = "Valued Customer",
    ticket_number: Optional[str] = None,
    include_footer: bool = True,
) -> str:
    """Format a response for email channel."""
    lines = [f"Hi {customer_name},\n"]
    lines.append(body)

    if ticket_number:
        lines.append(f"\n\n📋 Ticket Reference: {ticket_number}")

    if include_footer:
        lines.append(
            "\n\n---"
            "\n\nBest regards,\n"
            "TechFlow Analytics Support Team\n"
            "support@techflow.com | techflow.com\n"
            "Available 24/7 via chat, 8am-6pm PT via email"
        )

    return "\n".join(lines)


def format_whatsapp_response(
    body: str,
    max_chars: int = 300,
    break_paragraphs: bool = True,
) -> str:
    """Format a response for WhatsApp channel.

    WhatsApp has a 300 character limit per message (per spec).
    Returns a list of messages if needed.
    """
    # Clean and prepare text
    text = body.strip()

    if len(text) <= max_chars:
        return text

    # If too long, split by paragraphs or sentences
    if break_paragraphs:
        paragraphs = text.split("\n\n")
        messages = []
        current_message = ""

        for para in paragraphs:
            if len(current_message) + len(para) + 2 <= max_chars:
                if current_message:
                    current_message += "\n\n"
                current_message += para
            else:
                if current_message:
                    messages.append(current_message)
                current_message = para

        if current_message:
            messages.append(current_message)

        # Truncate if still too long
        return "\n\n---\n\n".join(
            [truncate_for_channel(msg, "whatsapp") for msg in messages]
        )
    else:
        return truncate_for_channel(text, "whatsapp", max_chars)


def format_web_form_response(
    body: str,
    ticket_number: Optional[str] = None,
    tracking_url: Optional[str] = None,
) -> str:
    """Format a response for web form channel."""
    lines = []

    if ticket_number:
        lines.append(f"✅ Your ticket {ticket_number} has been created.")

    lines.append(body)

    if tracking_url:
        lines.append(f"\n\n📍 Track your ticket: {tracking_url}")

    lines.append(
        "\n\n---\n\n"
        "Thank you for contacting TechFlow Analytics!\n"
        "We appreciate your feedback and will get back to you shortly."
    )

    return "\n".join(lines)


def format_escalation_message(
    original_message: str,
    customer_name: str,
    ticket_number: str,
    reason: str,
    priority: str = "high",
) -> Dict[str, Any]:
    """Format an escalation message for human support team."""
    return {
        "ticket_number": ticket_number,
        "customer_name": customer_name,
        "reason": reason,
        "priority": priority,
        "original_message": original_message,
        "escalation_type": "customer_request"
        if "escalate" in original_message.lower()
        else "system_escalation",
    }


def format_ticket_summary(
    ticket_data: Dict[str, Any],
    include_messages: bool = False,
    messages: list = None,
) -> str:
    """Format a ticket summary for display."""
    lines = [
        f"**Ticket #{ticket_data.get('ticket_number', 'N/A')}**",
        f"Status: {ticket_data.get('status', 'unknown').upper()}",
        f"Created: {ticket_data.get('created_at', 'N/A')}",
        f"Category: {ticket_data.get('category', 'General')}",
        f"Priority: {ticket_data.get('priority', 'Medium')}",
        f"Subject: {ticket_data.get('subject', 'N/A')}",
    ]

    if include_messages and messages:
        lines.append("\n**Recent Messages:**")
        for msg in messages[-3:]:  # Last 3 messages
            direction = "You" if msg.get("direction") == "outbound" else "Support"
            lines.append(
                f"[{msg.get('created_at', 'N/A')}] {direction}: {msg.get('content', '')[:100]}..."
            )

    return "\n".join(lines)


def split_long_message(text: str, max_length: int = 500, separator: str = "\n\n") -> list:
    """Split a long message into smaller chunks for WhatsApp delivery."""
    if len(text) <= max_length:
        return [text]

    messages = []
    current = ""

    parts = text.split(separator)
    for part in parts:
        if len(current) + len(part) + len(separator) <= max_length:
            if current:
                current += separator + part
            else:
                current = part
        else:
            if current:
                messages.append(current)
            current = part

    if current:
        messages.append(current)

    return messages


def sanitize_for_channel(text: str, channel: str) -> str:
    """Sanitize text for a specific channel."""
    if channel == "whatsapp":
        # WhatsApp doesn't support some markdown
        text = text.replace("**", "*")  # Bold to single asterisk
        text = text.replace("__", "_")  # Underline to single underscore

    elif channel == "email":
        # Email can use HTML, but keep it simple
        pass

    elif channel == "webform":
        # Web form can use HTML/markdown
        pass

    return text.strip()


def create_channel_specific_signature(channel: str) -> str:
    """Create a channel-appropriate signature."""
    if channel == "whatsapp":
        return (
            "\n\n---"
            "\n💬 TechFlow Analytics Support"
            "\n🕐 Available 24/7"
        )
    elif channel == "email":
        return (
            "\n\n---"
            "\n\nBest regards,\n"
            "TechFlow Analytics Support Team\n"
            "📧 support@techflow.com\n"
            "🌐 www.techflow.com\n"
            "⏰ Email support: 8am-6pm PT | Chat: 24/7"
        )
    elif channel == "webform":
        return "\n\n---\n\nThank you for contacting TechFlow Analytics!"
    else:
        return ""


def add_knowledge_base_source(
    response: str, kb_article_id: str, kb_title: str, kb_link: str = None
) -> str:
    """Add knowledge base source citation to response."""
    citation = f"\n\n📚 **Source:** {kb_title}"
    if kb_link:
        citation += f" - {kb_link}"

    return response + citation
