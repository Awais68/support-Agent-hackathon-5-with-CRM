"""N6: liveness and readiness are separate, and readiness fails when the DB does.

`/health` answered 200 with `{"status":"degraded","db":"error"}`, so k8s and
Render kept routing traffic to a pod that could not reach PostgreSQL. Now
`/livez` reports only that the process serves requests, `/readyz` (and the
legacy `/health`) return 503 when a dependency is down.
"""

from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from fastapi.testclient import TestClient

from api.main import app


class _Acquire:
    def __init__(self, conn=None, error=None):
        self._conn, self._error = conn, error

    async def __aenter__(self):
        if self._error:
            raise self._error
        return self._conn

    async def __aexit__(self, *exc):
        return False


def _pool(error=None):
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=1)
    pool = MagicMock()
    pool.acquire.side_effect = lambda: _Acquire(conn, error)
    return pool


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ENABLE_KAFKA", "true")
    monkeypatch.setattr(app.state, "kafka_enabled", True, raising=False)
    monkeypatch.setattr(app.state, "db_pool", _pool(), raising=False)
    return TestClient(app, raise_server_exceptions=False)


def _db_down(monkeypatch):
    error = asyncpg.exceptions.CannotConnectNowError("db down")
    monkeypatch.setattr(app.state, "db_pool", _pool(error), raising=False)


def test_ready_when_db_and_kafka_are_up(client):
    for path in ("/readyz", "/health"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.json() == {"status": "healthy", "db": "ok", "kafka": "ok"}


@pytest.mark.parametrize("path", ["/readyz", "/health"])
def test_db_down_is_503(client, monkeypatch, path):
    _db_down(monkeypatch)
    resp = client.get(path)
    assert resp.status_code == 503
    assert resp.json()["db"] == "error"


def test_no_pool_is_503_not_500(client, monkeypatch):
    monkeypatch.setattr(app.state, "db_pool", None, raising=False)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["db"] == "error"


def test_kafka_requested_but_down_is_503(client, monkeypatch):
    monkeypatch.setattr(app.state, "kafka_enabled", False, raising=False)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["kafka"] == "error"


def test_kafka_disabled_on_purpose_is_ready(client, monkeypatch):
    monkeypatch.setenv("ENABLE_KAFKA", "false")
    monkeypatch.setattr(app.state, "kafka_enabled", False, raising=False)
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["kafka"] == "disabled"


def test_liveness_ignores_dependencies(client, monkeypatch):
    _db_down(monkeypatch)
    monkeypatch.setattr(app.state, "kafka_enabled", False, raising=False)
    resp = client.get("/livez")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}


@pytest.mark.parametrize("path", ["/livez", "/readyz"])
def test_probes_need_no_api_key(client, path):
    assert client.get(path).status_code != 401


def test_hanging_db_is_a_fast_503(client, monkeypatch):
    # A paused/blackholed DB made /readyz hang with no response at all.
    import asyncio
    import time

    class _Hang(_Acquire):
        async def __aenter__(self):
            await asyncio.Event().wait()

    pool = MagicMock()
    pool.acquire.side_effect = lambda: _Hang()
    monkeypatch.setattr(app.state, "db_pool", pool, raising=False)
    monkeypatch.setenv("READINESS_DB_TIMEOUT_SECONDS", "0.2")
    started = time.monotonic()
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["db"] == "error"
    assert time.monotonic() - started < 2
