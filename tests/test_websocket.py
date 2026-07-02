"""Tests for the WebSocket connection manager."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from api.websocket_manager import WebSocketManager


@pytest.fixture
def ws_manager():
    return WebSocketManager()


@pytest.mark.asyncio
async def test_connect_disconnect(ws_manager):
    """Connect a client and disconnect it."""
    mock_ws = AsyncMock()
    await ws_manager.connect(mock_ws, "ticket-1")
    assert ws_manager.active_connections == 1
    assert ws_manager.active_tickets == 1

    await ws_manager.disconnect(mock_ws, "ticket-1")
    assert ws_manager.active_connections == 0
    assert ws_manager.active_tickets == 0


@pytest.mark.asyncio
async def test_multiple_clients_same_ticket(ws_manager):
    """Multiple WebSocket clients for the same ticket."""
    ws1 = AsyncMock()
    ws2 = AsyncMock()
    await ws_manager.connect(ws1, "ticket-1")
    await ws_manager.connect(ws2, "ticket-1")

    assert ws_manager.active_connections == 2
    assert ws_manager.active_tickets == 1

    await ws_manager.disconnect(ws1, "ticket-1")
    assert ws_manager.active_connections == 1
    assert ws_manager.active_tickets == 1

    await ws_manager.disconnect(ws2, "ticket-1")
    assert ws_manager.active_connections == 0
    assert ws_manager.active_tickets == 0


@pytest.mark.asyncio
async def test_multiple_tickets(ws_manager):
    """Clients for different tickets."""
    ws1 = AsyncMock()
    ws2 = AsyncMock()
    await ws_manager.connect(ws1, "ticket-a")
    await ws_manager.connect(ws2, "ticket-b")

    assert ws_manager.active_connections == 2
    assert ws_manager.active_tickets == 2


@pytest.mark.asyncio
async def test_broadcast_sends_to_all_clients(ws_manager):
    """Broadcast sends to every client for that ticket."""
    ws1 = AsyncMock()
    ws2 = AsyncMock()
    await ws_manager.connect(ws1, "ticket-1")
    await ws_manager.connect(ws2, "ticket-1")

    await ws_manager.broadcast("ticket-1", "ticket_update", {"status": "resolved"})

    ws1.send_text.assert_awaited_once()
    ws2.send_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_broadcast_only_target_ticket(ws_manager):
    """Broadcast only reaches clients for the specified ticket."""
    ws_target = AsyncMock()
    ws_other = AsyncMock()
    await ws_manager.connect(ws_target, "ticket-1")
    await ws_manager.connect(ws_other, "ticket-2")

    await ws_manager.broadcast("ticket-1", "ticket_update", {"status": "resolved"})

    ws_target.send_text.assert_awaited_once()
    ws_other.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_broadcast_removes_stale_connections(ws_manager):
    """Stale connections (send_text fails) are cleaned up."""
    ws_stale = AsyncMock()
    ws_stale.send_text.side_effect = Exception("Connection closed")
    ws_good = AsyncMock()

    await ws_manager.connect(ws_stale, "ticket-1")
    await ws_manager.connect(ws_good, "ticket-1")

    await ws_manager.broadcast("ticket-1", "ticket_update", {"status": "resolved"})

    ws_good.send_text.assert_awaited_once()
    assert ws_manager.active_connections == 1


@pytest.mark.asyncio
async def test_broadcast_ticket_update_convenience(ws_manager):
    """broadcast_ticket_update sends the correct event type."""
    ws = AsyncMock()
    await ws_manager.connect(ws, "ticket-1")

    await ws_manager.broadcast_ticket_update("ticket-1", {"status": "closed"})

    ws.send_text.assert_awaited_once()
    call_args = ws.send_text.await_args[0][0]
    assert '"event": "ticket_update"' in call_args


@pytest.mark.asyncio
async def test_broadcast_new_message_convenience(ws_manager):
    """broadcast_new_message sends the correct event type."""
    ws = AsyncMock()
    await ws_manager.connect(ws, "ticket-1")

    await ws_manager.broadcast_new_message("ticket-1", {"content": "Hello"})

    ws.send_text.assert_awaited_once()
    call_args = ws.send_text.await_args[0][0]
    assert '"event": "new_message"' in call_args


@pytest.mark.asyncio
async def test_accept_called_on_connect(ws_manager):
    """WebSocket.accept() is called when connecting."""
    mock_ws = AsyncMock()
    await ws_manager.connect(mock_ws, "ticket-1")
    mock_ws.accept.assert_awaited_once()


@pytest.mark.asyncio
async def test_disconnect_unknown_ticket(ws_manager):
    """Disconnecting from a non-existent ticket is a no-op."""
    mock_ws = AsyncMock()
    await ws_manager.disconnect(mock_ws, "nonexistent")
    assert ws_manager.active_connections == 0


@pytest.mark.asyncio
async def test_disconnect_unknown_client(ws_manager):
    """Disconnecting a client not in the set is a no-op."""
    ws1 = AsyncMock()
    ws2 = AsyncMock()
    await ws_manager.connect(ws1, "ticket-1")
    await ws_manager.disconnect(ws2, "ticket-1")
    assert ws_manager.active_connections == 1


@pytest.mark.asyncio
async def test_empty_broadcast_no_error(ws_manager):
    """Broadcasting to a ticket with no listeners does not error."""
    await ws_manager.broadcast("empty-ticket", "ping", {})
    assert ws_manager.active_connections == 0
