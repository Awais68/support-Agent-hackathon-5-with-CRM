"""File heartbeats for the worker's liveness/readiness probes (AUDIT N6/C2).

Each long-running loop refreshes ``<WORKER_HEARTBEAT_DIR>/<name>`` while it is
alive and not stuck; ``python -m utils.worker_healthcheck`` fails when any of those
files is stale. Files, not HTTP, because the worker serves no port.
"""

import os
from pathlib import Path

DEFAULT_DIR = "/tmp/techflow-worker-heartbeat"


def heartbeat_dir() -> Path:
    return Path(os.getenv("WORKER_HEARTBEAT_DIR", DEFAULT_DIR))


def interval_seconds() -> float:
    return float(os.getenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "10"))


def stall_seconds() -> float:
    """How long one handler call may run before the loop counts as stuck."""
    return float(os.getenv("WORKER_STALL_SECONDS", "600"))


def beat(name: str) -> None:
    directory = heartbeat_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.touch()
    os.utime(path)


def reset() -> None:
    """Drop heartbeats left over from a previous run of this container."""
    directory = heartbeat_dir()
    if directory.is_dir():
        for path in directory.iterdir():
            if path.is_file():
                path.unlink(missing_ok=True)
