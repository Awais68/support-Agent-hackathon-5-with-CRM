"""N3: a chaos experiment passes only when nothing went wrong.

`run()` reported "passed" whenever `verify_recovery()` returned True, even if
the failure was never injected (disconnect/crash command failed) or the
degraded behaviour was wrong. Experiment 05 "passed" while the reconnect had
failed and the worker stayed partitioned.
"""

from types import SimpleNamespace

from chaos.experiments.base import ChaosExperiment
from chaos.experiments.network_partition import NetworkPartitionExperiment


class _Exp(ChaosExperiment):
    name = "test"

    def __init__(self, inject_error=None, recovered=True):
        super().__init__(SimpleNamespace())
        self._inject_error, self._recovered = inject_error, recovered

    def pre_check(self):
        return True

    def inject(self):
        if self._inject_error:
            self.errors.append(self._inject_error)

    def verify_recovery(self):
        return self._recovered

    def log(self, message):
        pass


def test_clean_run_passes():
    assert _Exp().run().status == "passed"


def test_failed_injection_is_not_a_pass():
    result = _Exp(inject_error="docker network disconnect failed").run()
    assert result.status == "failed"
    assert "docker network disconnect failed" in result.errors_observed


def test_no_recovery_fails():
    assert _Exp(recovered=False).run().status == "failed"


def _partition(monkeypatch, calls):
    config = SimpleNamespace(
        docker_network="",
        container_names={"worker": "w"},
        disruption_duration=0,
    )
    exp = NetworkPartitionExperiment(config)
    monkeypatch.setattr(exp, "log", lambda m: None)
    monkeypatch.setattr("chaos.experiments.network_partition.time.sleep", lambda s: None)
    monkeypatch.setattr(exp, "pre_check", lambda: True)
    monkeypatch.setattr(exp, "_wait_for_system_and_worker", lambda: True)

    attached = {"net": True}

    def run_cmd(cmd, shell=False):
        calls.append(cmd)
        if cmd[:3] == ["docker", "network", "disconnect"]:
            attached["net"] = False
        elif cmd[:3] == ["docker", "network", "connect"]:
            attached["net"] = cmd[3] == "spaudit_techflow"
            return (0, "") if cmd[3] else (1, "invalid network name")
        elif cmd[:2] == ["docker", "inspect"]:
            # A disconnected worker has no network left to read.
            return 0, "spaudit_techflow " if attached["net"] else ""
        elif cmd[:2] == ["docker", "exec"]:
            return (0, "") if attached["net"] else (1, "")
        return 0, ""

    monkeypatch.setattr(exp, "_run_cmd", run_cmd)
    return exp


def test_partition_reconnects_to_the_network_it_left(monkeypatch):
    calls = []
    result = _partition(monkeypatch, calls).run()
    assert ["docker", "network", "connect", "spaudit_techflow", "w"] in calls
    assert result.status == "passed", result.errors_observed


def test_partition_fails_when_the_reconnect_fails(monkeypatch):
    calls = []
    exp = _partition(monkeypatch, calls)
    real = exp._run_cmd

    def connect_fails(cmd, shell=False):
        if cmd[:3] == ["docker", "network", "connect"]:
            calls.append(cmd)
            return 1, "network not found"
        return real(cmd, shell)

    exp._run_cmd = connect_fails
    assert exp.run().status == "failed"
