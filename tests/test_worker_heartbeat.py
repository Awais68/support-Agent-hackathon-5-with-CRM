"""N6/C2: the worker probes check that the consumers make progress.

The k8s worker probes ran `sys.exit(0)` and compose ran `kill -0 1`, so a
worker whose consumer loop had died or hung stayed "healthy" forever. Each
consumer now refreshes a heartbeat file while it is alive and not stuck in a
handler; `python -m utils.worker_healthcheck` fails when any heartbeat is stale.
"""

import asyncio
import os
import time

import pytest

from kafka_client import KafkaConsumerClient, KafkaMessage
from utils import heartbeat
from utils import worker_healthcheck as healthcheck


@pytest.fixture
def hb_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKER_HEARTBEAT_DIR", str(tmp_path))
    monkeypatch.setenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "0.05")
    return tmp_path


def _age(path, seconds):
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_fresh_heartbeats_pass(hb_dir):
    heartbeat.beat("a")
    heartbeat.beat("b")
    assert healthcheck.check(max_age=30) == []


def test_stale_heartbeat_fails(hb_dir):
    heartbeat.beat("a")
    heartbeat.beat("b")
    _age(hb_dir / "b", 120)
    problems = healthcheck.check(max_age=30)
    assert len(problems) == 1 and "b" in problems[0]


def test_no_heartbeat_at_all_fails(hb_dir):
    assert healthcheck.check(max_age=30) != []


def test_missing_dir_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKER_HEARTBEAT_DIR", str(tmp_path / "nope"))
    assert healthcheck.check(max_age=30) != []


def test_cli_exit_codes(hb_dir):
    heartbeat.beat("a")
    assert healthcheck.main(["--max-age", "30"]) == 0
    _age(hb_dir / "a", 120)
    assert healthcheck.main(["--max-age", "30"]) == 1


def test_reset_removes_leftovers(hb_dir):
    heartbeat.beat("old")
    heartbeat.reset()
    assert list(hb_dir.iterdir()) == []


class _IdleConsumer:
    """Never yields a message, like a quiet topic."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Event().wait()


class _OneMessage:
    def __init__(self, raw):
        self._raw = raw
        self.commit = None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._raw is None:
            await asyncio.Event().wait()
        raw, self._raw = self._raw, None
        return raw


async def test_idle_consumer_keeps_beating(hb_dir):
    client = KafkaConsumerClient("kafka:9092", "idle-group")
    client.consumer = _IdleConsumer()
    task = asyncio.create_task(client.consume_messages(on_message=lambda m: None))
    try:
        await asyncio.sleep(0.2)
        assert healthcheck.check(max_age=0.15) == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    # A cancelled consumer stops beating.
    await asyncio.sleep(0.2)
    assert healthcheck.check(max_age=0.15) != []


async def test_stuck_handler_stops_beating(hb_dir, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("WORKER_STALL_SECONDS", "0.1")
    raw = SimpleNamespace(
        topic="inbound.email",
        partition=0,
        offset=1,
        value=KafkaMessage("inbound.email", {"x": 1}, message_id="m").to_json(),
    )
    client = KafkaConsumerClient("kafka:9092", "stuck-group")
    client.consumer = _OneMessage(raw)

    async def hang(_message):
        await asyncio.Event().wait()

    task = asyncio.create_task(client.consume_messages(on_message=hang))
    try:
        await asyncio.sleep(0.5)
        assert healthcheck.check(max_age=0.2) != []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
