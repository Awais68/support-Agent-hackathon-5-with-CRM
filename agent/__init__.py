"""TechFlow CRM Digital FTE Agent module."""

from agent.formatters import (
    format_email_response,
    format_web_form_response,
    format_whatsapp_response,
    truncate_for_channel,
)
from agent.pre_processing_gate import GateAction, GateResult, run_gate
from agent.prompts import CHANNEL_ADDENDUMS, CLASSIFICATION_PROMPT, SYSTEM_PROMPT
from agent.sentiment_analyzer import analyze_sentiment
from agent.tools import (
    AGENT_TOOLS,
    create_ticket,
    escalate_to_human,
    get_customer_history,
    search_knowledge_base,
    send_response,
)

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
