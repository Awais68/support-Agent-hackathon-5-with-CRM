import json
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass
class ExperimentResult:
    experiment_name: str
    status: str  # "passed" | "failed" | "error"
    recovery_time_seconds: float | None
    errors_observed: list[str]
    started_at: str
    finished_at: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)

    def passed(self) -> bool:
        return self.status == "passed"


class ChaosExperiment(ABC):
    def __init__(self, config):
        self.config = config
        self.errors: list[str] = []
        self._start_time: float | None = None

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def inject(self) -> None:
        pass

    @abstractmethod
    def verify_recovery(self) -> bool:
        pass

    def pre_check(self) -> bool:
        return self._health_check()

    def _health_check(self) -> bool:
        import httpx

        try:
            r = httpx.get(
                f"{self.config.api_url}/health",
                timeout=5,
                headers={"X-API-Key": self.config.api_key},
            )
            if r.status_code == 200:
                data = r.json()
                return data.get("status") == "healthy"
            return False
        except Exception as e:
            self.errors.append(f"Health check failed: {e}")
            return False

    def _wait_for_healthy(
        self,
        timeout: int | None = None,
        interval: float | None = None,
    ) -> bool:
        import httpx

        timeout = timeout or self.config.recovery_timeout
        interval = interval or self.config.recovery_interval
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                r = httpx.get(
                    f"{self.config.api_url}/health",
                    timeout=5,
                    headers={"X-API-Key": self.config.api_key},
                )
                if r.status_code == 200:
                    data = r.json()
                    if data.get("status") == "healthy":
                        return True
            except Exception:
                pass
            time.sleep(interval)

        self.errors.append(f"System did not recover within {timeout}s timeout")
        return False

    def _run_cmd(self, cmd: list[str], shell: bool = False) -> tuple[int, str]:
        import subprocess

        try:
            result = subprocess.run(
                cmd if not shell else " ".join(cmd),
                shell=shell,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode, result.stdout + result.stderr
        except subprocess.TimeoutExpired as e:
            self.errors.append(f"Command timed out: {' '.join(cmd)}")
            return -1, str(e)
        except FileNotFoundError:
            self.errors.append(f"Command not found: {cmd[0]}")
            return -1, ""

    def _docker_compose(self, action: str, *services: str) -> tuple[int, str]:
        cmd = ["docker", "compose"]
        cmd.extend(self.config.compose_flags)
        cmd.append(action)
        cmd.extend(services)
        return self._run_cmd(cmd)

    def _docker(self, action: str, container: str, *args: str) -> tuple[int, str]:
        return self._run_cmd(["docker", action, container, *args])

    def _crash(self, container: str) -> tuple[int, str]:
        """Make the container's main process exit, like a crash.

        `docker kill` counts as a manual stop, so Docker never applies the
        restart policy and the experiment can only fail. Signalling PID 1
        from inside the container makes it exit on its own, which is what the
        restart policy (compose) or the kubelet (k8s) has to recover from.
        """
        return self._docker(
            "exec", container, "python", "-c", "import os, signal; os.kill(1, signal.SIGTERM)"
        )

    def _container_health(self, container: str) -> str:
        code, output = self._docker("inspect", container, "--format", "{{.State.Health.Status}}")
        return output.strip() if code == 0 else "missing"

    def _wait_for_container_healthy(self, container: str, timeout: int | None = None) -> bool:
        """Wait for the container's own healthcheck (the worker heartbeat probe)."""
        timeout = timeout or self.config.recovery_timeout
        deadline = time.time() + timeout
        status = "unknown"
        while time.time() < deadline:
            status = self._container_health(container)
            if status == "healthy":
                return True
            time.sleep(self.config.recovery_interval)
        self.errors.append(f"{container} not healthy within {timeout}s (last: {status})")
        return False

    def _wait_for_system_and_worker(self) -> bool:
        # API health alone passed even when the worker never came back.
        return self._wait_for_healthy() and self._wait_for_container_healthy(
            self.config.container_names["worker"]
        )

    def log(self, message: str) -> None:
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        print(f"[{ts}] [{self.name}] {message}")

    def run(self) -> ExperimentResult:
        started_at = datetime.now(UTC).isoformat()
        self._start_time = time.time()
        self.log("Starting experiment")

        if not self.pre_check():
            self.log("Pre-check failed — system not healthy before experiment")
            return ExperimentResult(
                experiment_name=self.name,
                status="error",
                recovery_time_seconds=None,
                errors_observed=self.errors + ["System not healthy before experiment"],
                started_at=started_at,
                finished_at=datetime.now(UTC).isoformat(),
            )

        self.log("Injecting failure")
        try:
            self.inject()
        except Exception as e:
            self.log(f"Injection failed: {e}")
            return ExperimentResult(
                experiment_name=self.name,
                status="error",
                recovery_time_seconds=None,
                errors_observed=self.errors + [f"Injection error: {e}"],
                started_at=started_at,
                finished_at=datetime.now(UTC).isoformat(),
            )

        recovery_start = time.time()
        self.log("Waiting for recovery")
        recovered = self.verify_recovery()
        recovery_time = time.time() - recovery_start

        # An error during inject means the failure was not injected or the
        # degraded behaviour was wrong; recovering from that proves nothing.
        if recovered and not self.errors:
            status = "passed"
            self.log(f"Recovered in {recovery_time:.1f}s")
        elif recovered:
            status = "failed"
            self.log(f"Recovered, but errors were observed: {self.errors}")
        else:
            status = "failed"
            self.log(f"Did not recover within timeout ({recovery_time:.1f}s)")

        return ExperimentResult(
            experiment_name=self.name,
            status=status,
            recovery_time_seconds=round(recovery_time, 1),
            errors_observed=self.errors,
            started_at=started_at,
            finished_at=datetime.now(UTC).isoformat(),
        )
