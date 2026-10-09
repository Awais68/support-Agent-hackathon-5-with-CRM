"""S6: the database password never reaches the logs.

The worker and the seed script logged DATABASE_URL verbatim at startup,
password included (seen in `docker logs techflow-worker`).
"""

import sys
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from utils.redact import redact_dsn

PASSWORD = "s3cr3t-Pa55"
DSN = f"postgresql://techflow:{PASSWORD}@postgres:5432/techflow?sslmode=require"


class _StopError(Exception):
    pass


async def _startup_logs(module_main, monkeypatch, argv=None):
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setattr(sys, "argv", argv or ["prog"])
    with (
        capture_logs() as logs,
        patch("asyncpg.create_pool", side_effect=_StopError),
        pytest.raises(_StopError),
    ):
        await module_main()
    return logs


@pytest.mark.asyncio
async def test_worker_startup_does_not_log_password(monkeypatch):
    from workers import message_processor

    monkeypatch.setattr(message_processor, "load_environment", lambda: None)
    logs = await _startup_logs(message_processor.main, monkeypatch)
    assert logs, "expected a startup log line"
    assert PASSWORD not in repr(logs)
    assert any("postgres:5432/techflow" in repr(e) for e in logs)


@pytest.mark.asyncio
async def test_seed_does_not_log_password(monkeypatch):
    from database import seed

    monkeypatch.setattr(seed, "load_environment", lambda: None)
    logs = await _startup_logs(seed.main, monkeypatch)
    assert logs
    assert PASSWORD not in repr(logs)


@pytest.mark.parametrize(
    "dsn, expected",
    [
        (DSN, "postgresql://techflow:***@postgres:5432/techflow?sslmode=require"),
        ("postgresql+asyncpg://u:p%40ss@h/db", "postgresql+asyncpg://u:***@h/db"),
        ("redis://:pw@redis:6379/0", "redis://:***@redis:6379/0"),
        ("postgresql://localhost/techflow", "postgresql://localhost/techflow"),
        ("not a url", "not a url"),
        (None, ""),
    ],
)
def test_redact_dsn(dsn, expected):
    assert redact_dsn(dsn) == expected
