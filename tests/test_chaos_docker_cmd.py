"""Chaos recovery checks call `_docker("exec", container, cmd...)`.

`_docker` took exactly two arguments, so the Postgres and Kafka recovery
checks raised TypeError instead of probing the container (found by mypy, N16).
"""

from chaos.experiments.postgres_outage import PostgresOutageExperiment


def test_docker_exec_passes_command_args(monkeypatch):
    exp = PostgresOutageExperiment.__new__(PostgresOutageExperiment)
    seen = []
    monkeypatch.setattr(exp, "_run_cmd", lambda cmd, shell=False: (seen.append(cmd) or (0, "")))
    assert exp._docker("exec", "pg", "pg_isready", "-U", "techflow") == (0, "")
    assert seen == [["docker", "exec", "pg", "pg_isready", "-U", "techflow"]]
