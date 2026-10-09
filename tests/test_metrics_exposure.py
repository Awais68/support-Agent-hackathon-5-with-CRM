"""S11: Prometheus metrics are not served on the public API port.

`/metrics` sat on the public app without auth and went out through ingress
`/`, so anyone could read internal counters and sentiment metrics. Metrics
now live on a separate internal port (METRICS_PORT) that ingress, Render and
the compose host mapping do not expose; Prometheus scrapes that port.
"""

import urllib.request

from fastapi.testclient import TestClient

from api.main import app, start_metrics_server


def test_public_port_does_not_serve_metrics():
    # Exploit: unauthenticated GET /metrics on the public app returned 200.
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/metrics")
    assert resp.status_code == 401
    assert "python_info" not in resp.text
    # Not even a key holder gets them there: the route is gone.
    resp = client.get("/metrics", headers={"X-API-Key": "test-key-12345"})
    assert resp.status_code == 404


def test_metrics_are_served_on_the_internal_port():
    server = start_metrics_server(port=0, addr="127.0.0.1")
    try:
        port = server.server_port
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as resp:
            assert resp.status == 200
            assert b"python_info" in resp.read()
    finally:
        server.shutdown()
        server.server_close()


def test_metrics_port_zero_from_env_disables_the_server(monkeypatch):
    monkeypatch.setenv("METRICS_PORT", "0")
    assert start_metrics_server() is None
