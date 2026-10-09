"""N4: a worker killed mid-message reprocesses it, and exactly once.

Real Kafka and PostgreSQL; the worker runs as a subprocess
(tests/inbound_worker_harness.py) with a scripted LLM so it can be SIGKILLed
at a precise point. Needs OUTBOUND_TEST_KAFKA and OUTBOUND_TEST_DATABASE_URL
pointing at a migrated database (the compose stack); skipped otherwise.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import asyncpg
import pytest

from kafka_client import INBOUND_EMAIL_TOPIC, KafkaMessage

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

ROOT = Path(__file__).resolve().parent.parent
KAFKA = os.getenv("OUTBOUND_TEST_KAFKA")
DB = (os.getenv("OUTBOUND_TEST_DATABASE_URL") or "").replace(
    "postgresql+asyncpg://", "postgresql://"
)


@pytest.fixture
async def env(tmp_path):
    if not KAFKA or not DB:
        pytest.skip("OUTBOUND_TEST_KAFKA / OUTBOUND_TEST_DATABASE_URL not set")
    from aiokafka import AIOKafkaProducer
    from aiokafka.admin import AIOKafkaAdminClient, NewTopic

    try:
        pool = await asyncpg.create_pool(DB, min_size=1, max_size=2, timeout=3)
    except (OSError, asyncpg.PostgresError) as e:
        pytest.skip(f"PostgreSQL not reachable: {e}")
    migration = ROOT / "database/migrations/012_inbound_processing.sql"
    if migration.exists():
        await pool.execute(migration.read_text())

    topic = f"inbound.redelivery-test-{uuid.uuid4().hex[:8]}"
    admin = AIOKafkaAdminClient(bootstrap_servers=KAFKA)
    await admin.start()
    await admin.create_topics([NewTopic(topic, num_partitions=1, replication_factor=1)])
    producer = AIOKafkaProducer(bootstrap_servers=KAFKA)
    await producer.start()

    ctx = {
        "pool": pool,
        "topic": topic,
        "group": f"redelivery-test-{uuid.uuid4().hex[:6]}",
        "events": tmp_path / "events.jsonl",
        "producer": producer,
        "procs": [],
    }
    try:
        yield ctx
    finally:
        for p in ctx["procs"]:
            if p.poll() is None:
                p.kill()
                p.wait()
        await producer.stop()
        await admin.delete_topics([topic])
        await admin.close()
        await pool.close()


def _spawn(env, mode):
    proc_env = {
        **os.environ,
        "HARNESS_MODE": mode,
        "HARNESS_EVENTS": str(env["events"]),
        "HARNESS_DB": DB,
        "HARNESS_KAFKA": KAFKA,
        "HARNESS_TOPIC": env["topic"],
        "HARNESS_GROUP": env["group"],
        "PYTHONPATH": str(ROOT),
    }
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "tests/inbound_worker_harness.py")],
        env=proc_env,
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=open(env["events"].with_suffix(f".{mode}.log"), "w"),
    )
    env["procs"].append(proc)
    return proc


def _events(env):
    if not env["events"].exists():
        return []
    return [json.loads(line) for line in env["events"].read_text().splitlines() if line]


async def _wait_for(env, predicate, timeout, what):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(predicate(e) for e in _events(env)):
            return
        await asyncio.sleep(0.2)
    logs = {p.name: p.read_text()[-1500:] for p in env["events"].parent.glob("*.log")}
    pytest.fail(f"timed out waiting for {what}; events={_events(env)}; logs={logs}")


async def _publish(env, email, message_id):
    message = KafkaMessage(
        INBOUND_EMAIL_TOPIC,
        {
            "customer_email": email,
            "sender_name": "Redelivery Test",
            "subject": "Password reset",
            "body": "How do I reset my password?",
        },
        message_id=message_id,
    )
    await env["producer"].send_and_wait(env["topic"], message.to_json().encode())


def _kill(proc):
    proc.send_signal(signal.SIGKILL)
    proc.wait()


async def _outcome(env, email):
    pool = env["pool"]
    tickets = await pool.fetch(
        "SELECT t.id FROM tickets t JOIN customers c ON c.id = t.customer_id "
        "WHERE lower(c.email) = $1",
        email,
    )
    ticket_ids = {str(t["id"]) for t in tickets}
    completed_runs = await pool.fetchval(
        "SELECT count(*) FROM agent_runs WHERE ticket_id = ANY($1::uuid[]) "
        "AND status = 'completed'",
        list(ticket_ids),
    )
    replies = [
        e
        for e in _events(env)
        if e["event"] == "publish"
        and e["topic"] == "notifications.outbound"
        and e["payload"].get("agent_run_id")
        and e["payload"].get("ticket_id") in ticket_ids
    ]
    return ticket_ids, completed_runs, replies


async def test_killed_mid_agent_is_reprocessed_exactly_once(env):
    email = f"redelivery-{uuid.uuid4().hex[:8]}@example.com"
    await _publish(env, email, str(uuid.uuid4()))

    first = _spawn(env, "hang_in_agent")
    await _wait_for(env, lambda e: e["event"] == "in_agent", 60, "first worker inside the agent")
    # Longer than aiokafka's 5 s auto-commit interval, so the old consumer
    # had committed the offset before it died.
    await asyncio.sleep(6)
    _kill(first)

    second = _spawn(env, "normal")
    await _wait_for(
        env, lambda e: e["event"] == "handled" and e["pid"] == second.pid, 60, "reprocessing"
    )
    await asyncio.sleep(3)  # anything else queued would show up by now
    _kill(second)

    tickets, completed_runs, replies = await _outcome(env, email)
    assert len(tickets) == 1, "the message created more than one ticket"
    assert completed_runs == 1
    assert len(replies) == 1, replies


async def test_killed_after_agent_does_not_reply_twice(env):
    email = f"redelivery-{uuid.uuid4().hex[:8]}@example.com"
    await _publish(env, email, str(uuid.uuid4()))

    first = _spawn(env, "hang_after_agent")
    await _wait_for(env, lambda e: e["event"] == "after_agent", 60, "first worker finishing")
    await asyncio.sleep(6)
    _kill(first)

    second = _spawn(env, "normal")
    await _wait_for(
        env, lambda e: e["event"] == "handled" and e["pid"] == second.pid, 60, "redelivery"
    )
    await asyncio.sleep(3)
    _kill(second)

    tickets, completed_runs, replies = await _outcome(env, email)
    assert len(tickets) == 1
    assert completed_runs == 1, "the agent ran again for an already-answered message"
    assert len(replies) == 1, replies


async def test_failed_attempt_is_retried_and_answered(env):
    """An LLM error on the first try must not count as an answered message."""
    email = f"redelivery-{uuid.uuid4().hex[:8]}@example.com"
    await _publish(env, email, str(uuid.uuid4()))

    worker = _spawn(env, "fail_once")
    await _wait_for(env, lambda e: e["event"] == "handled", 60, "retry to finish")
    await asyncio.sleep(2)
    _kill(worker)

    assert any(e["event"] == "llm_failed" for e in _events(env))
    tickets, completed_runs, replies = await _outcome(env, email)
    assert len(tickets) == 1
    assert completed_runs == 1
    assert len(replies) == 1, replies
