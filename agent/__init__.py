"""TechFlow CRM Digital FTE Agent module."""

from agent.tools import (
    search_knowledge_base,
    create_ticket,
    get_customer_history,
    escalate_to_human,
    send_response,
    AGENT_TOOLS,
)
from agent.prompts import SYSTEM_PROMPT, CHANNEL_ADDENDUMS, CLASSIFICATION_PROMPT
from agent.formatters import (
    format_email_response,
    format_whatsapp_response,
    format_web_form_response,
    truncate_for_channel,
)
from agent.pre_processing_gate import run_gate, GateAction, GateResult
from agent.sentiment_analyzer import analyze_sentiment

__all__ = [
    "search_knowledge_base",
    "create_ticket",
    "get_customer_history",
    "escalate_to_human",
    "send_response",
    "AGENT_TOOLS",
    "SYSTEM_PROMPT",
    "CHANNEL_ADDENDUMS",
    "CLASSIFICATION_PROMPT",
    "format_email_response",
    "format_whatsapp_response",
    "format_web_form_response",
    "truncate_for_channel",
    "run_gate",
    "GateAction",
    "GateResult",
    "analyze_sentiment",
]
