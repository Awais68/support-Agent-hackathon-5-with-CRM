"""WebSocket connection manager for real-time ticket updates."""

import asyncio
import json
import structlog
from typing import Dict, Set, Any
from exceptions import sanitize_error_message

from fastapi import WebSocket

logger = structlog.get_logger(__name__)


class WebSocketManager:
    """Manages WebSocket connections grouped by ticket_id.

    Maintains a map of ticket_id → set of WebSocket connections,
    and broadcasts JSON-encoded ticket updates to all connected
    clients for a given ticket.
    """

    def __init__(self) -> None:
        self._connections: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, ticket_id: str) -> None:
        await websocket.accept()
        async with self._lock:
            if ticket_id not in self._connections:
                self._connections[ticket_id] = set()
            self._connections[ticket_id].add(websocket)
        logger.debug("ws connected", ticket_id=ticket_id)

    async def disconnect(self, websocket: WebSocket, ticket_id: str) -> None:
        async with self._lock:
            conns = self._connections.get(ticket_id)
            if conns:
                conns.discard(websocket)
                if not conns:
                    del self._connections[ticket_id]
        logger.debug("ws disconnected", ticket_id=ticket_id)

    async def broadcast(self, ticket_id: str, event: str, data: dict[str, Any]) -> None:
        payload = json.dumps({"event": event, "data": data}, default=str)
        async with self._lock:
            conns = self._connections.get(ticket_id, set()).copy()
        stale = set()
        for ws in conns:
            try:
                await ws.send_text(payload)
            except Exception as e:
                logger.warning("ws broadcast failed", ticket_id=ticket_id, error=sanitize_error_message(str(e)))
                stale.add(ws)
        if stale:
            async with self._lock:
                conns = self._connections.get(ticket_id)
                if conns:
                    conns.difference_update(stale)
                    if not conns:
                        del self._connections[ticket_id]

    async def broadcast_ticket_update(self, ticket_id: str, ticket_data: dict[str, Any]) -> None:
        await self.broadcast(ticket_id, "ticket_update", ticket_data)

    async def broadcast_new_message(self, ticket_id: str, ticket_data: dict[str, Any]) -> None:
        await self.broadcast(ticket_id, "new_message", ticket_data)

    @property
    def active_connections(self) -> int:
        return sum(len(conns) for conns in self._connections.values())

    @property
    def active_tickets(self) -> int:
        return len(self._connections)
