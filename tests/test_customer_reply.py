"""The outbound reply is written to the customer, never the agent's own notes.

The model and database are mocked; the real agent loop, send_response tool,
formatters and reply guard run.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from agent.customer_success_agent import AgentContext, CustomerSuccessAgent
from agent.pre_processing_gate import GateAction, GateResult
from agent.reply_guard import operator_voice_reason
from agent.tools import ToolContext, send_response

TICKET_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
CUSTOMER_ID = UUID("550e8400-e29b-41d4-a716-446655440001")
RUN_ID = UUID("550e8400-e29b-41d4-a716-446655440009")

CUSTOMER_TEXT = (
    "You can export your dashboard as CSV from Reports > Export. "
    "Exports up to 1M rows are included in your plan."
)
OPERATOR_NOTE = "I've responded to the customer with the CSV export steps from the KB article."


def _tool_call(name: str, args: dict, call_id: str = "call_1"):
    tc = MagicMock(id=call_id)
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    return tc


def _completion(content=None, tool_calls=None):
    message = MagicMock(content=content, tool_calls=tool_calls)
    message.model_dump.return_value = {"role": "assistant", "content": content}
    return MagicMock(choices=[MagicMock(message=message)], usage=None)


def _model(*loop_responses):
    """Classification call first, then the scripted agent-loop turns."""
    turns = iter(loop_responses)

    async def create(**kwargs):
        if "tools" not in kwargs:
            return _completion(content="Technical/Product")
        return next(turns)

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=create)
    return client


async def _run(openai_client, channel: str):
    producer = MagicMock()
    producer.send_message = AsyncMock(return_value="msg-id")
    agent = CustomerSuccessAgent(
        AgentContext(db_pool=MagicMock(), kafka_producer=producer, openai_client=openai_client)
    )
    with (
        patch("agent.customer_success_agent.run_gate", new_callable=AsyncMock) as gate,
        patch("database.queries.create_agent_run", AsyncMock(return_value={"id": RUN_ID})),
        patch("database.queries.complete_agent_run", AsyncMock()),
        patch("database.queries.update_message_sentiment", AsyncMock()),
        patch("database.queries.get_message_sentiment_history", AsyncMock(return_value=[])),
        patch(
            "database.queries.get_ticket",
            AsyncMock(return_value={"customer_id": CUSTOMER_ID, "ticket_number": "TKT-1"}),
        ),
        patch("database.queries.add_message", AsyncMock(return_value={"id": "m1"})) as add,
        patch("database.queries.update_ticket_status", AsyncMock()) as status,
    ):
        gate.return_value = GateResult(action=GateAction.ALLOW, reason="ok", sentiment_score=0.6)
        result = await agent.process_customer_message(
            ticket_id=TICKET_ID,
            customer_id=CUSTOMER_ID,
            customer_email="jane@example.com",
            customer_name="Jane",
            message="How do I export to CSV?",
            channel=channel,
            ticket_number="TKT-1",
        )
    final = [
        c.args[1]
        for c in producer.send_message.await_args_list
        if c.args[0] == "notifications.outbound" and "agent_run_id" in c.args[1]
    ]
    assert len(final) == 1
    return result, final[0], add, status


def _send(channel: str):
    return _tool_call(
        "send_response",
        {
            "ticket_id": str(TICKET_ID),
            "customer_email": "jane@example.com",
            "channel": channel,
            "response_body": CUSTOMER_TEXT,
        },
    )


@pytest.mark.asyncio
async def test_email_payload_carries_customer_reply_not_operator_note():
    client = _model(_completion(tool_calls=[_send("email")]), _completion(content=OPERATOR_NOTE))
    result, payload, _, status = await _run(client, "email")

    reply = payload["customer_reply"]
    assert reply.startswith("Hi Jane,")
    assert CUSTOMER_TEXT in reply
    assert operator_voice_reason(reply) is None
    assert "responded to the customer" not in reply
    assert payload["internal_note"] == OPERATOR_NOTE
    assert "message" not in payload  # the old field the sender used to deliver
    assert result["response"] == reply and result["reply_persisted"] is True
    status.assert_not_awaited()


@pytest.mark.asyncio
async def test_whatsapp_payload_carries_customer_reply_not_operator_note():
    client = _model(_completion(tool_calls=[_send("whatsapp")]), _completion(content=OPERATOR_NOTE))
    _, payload, _, _ = await _run(client, "whatsapp")

    assert payload["customer_reply"] == CUSTOMER_TEXT
    assert payload["internal_note"] == OPERATOR_NOTE


@pytest.mark.asyncio
async def test_operator_voiced_final_text_is_blocked_and_ticket_escalated():
    # The model skipped send_response and only wrote a note about the customer.
    client = _model(_completion(content="I've replied to the customer about the CSV export."))
    result, payload, _, status = await _run(client, "email")

    assert payload["customer_reply"] == ""
    assert "Reply blocked" in payload["internal_note"]
    assert result["response"] == "" and result["escalated"] is True
    status.assert_awaited_once()
    assert status.await_args.args[2] == "escalated"


@pytest.mark.asyncio
async def test_rejected_send_response_is_not_used_as_the_reply():
    bad = _tool_call(
        "send_response",
        {
            "ticket_id": str(TICKET_ID),
            "channel": "email",
            "response_body": "The customer was told to use Reports > Export.",
        },
    )
    client = _model(
        _completion(tool_calls=[bad]),
        _completion(tool_calls=[_send("email")], content=None),
        _completion(content=OPERATOR_NOTE),
    )
    _, payload, add, _ = await _run(client, "email")

    # Only the rewritten, customer-voiced reply was stored and sent.
    assert [c.kwargs["content"] for c in add.await_args_list] == [CUSTOMER_TEXT]
    assert CUSTOMER_TEXT in payload["customer_reply"]


@pytest.mark.asyncio
async def test_send_response_tool_rejects_operator_voice():
    db_pool = MagicMock()
    producer = MagicMock()
    producer.send_message = AsyncMock()
    ctx = ToolContext(db_pool=db_pool, kafka_producer=producer, openai_client=MagicMock())
    with patch("database.queries.add_message", AsyncMock()) as add:
        out = await send_response(
            {"ticket_id": str(TICKET_ID), "response_body": OPERATOR_NOTE}, ctx
        )
    assert out["sent"] is False and "second person" in out["error"]
    add.assert_not_awaited()
    producer.send_message.assert_not_awaited()


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        OPERATOR_NOTE,
        "The customer is asking about CSV exports; I sent the KB link.",
        "I have replied to them with the reset steps.",
        "Escalated via escalate_to_human because the user's account is locked.",
        "I've sent a response explaining the rate limits.",
    ],
)
def test_guard_flags_empty_or_operator_text(text):
    assert operator_voice_reason(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        CUSTOMER_TEXT,
        "Hi Jane, our customer success team will reach out within 8 hours.",
        "I've escalated your request to our support team. A human agent will follow up.",
        "Thanks for being a valued customer! You can reset your password in Settings.",
        "Please check the customer portal for your invoices.",
    ],
)
def test_guard_allows_customer_voiced_text(text):
    assert operator_voice_reason(text) is None
