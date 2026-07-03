import time

from chaos.experiments.base import ChaosExperiment


class MetricsPipelineExperiment(ChaosExperiment):
    name = "06_metrics_pipeline"

    def inject(self) -> None:
        self.log(f"Stopping Prometheus container: {self.config.container_names['prometheus']}")
        code, output = self._docker("stop", self.config.container_names["prometheus"])
        if code != 0:
            self.errors.append(f"docker stop prometheus failed: {output}")

        self.log("Verifying API/worker continue functioning without Prometheus")
        import httpx
        deadline = time.time() + 15
        system_ok = False
        metrics_ok = False
        while time.time() < deadline:
            try:
                r = httpx.get(f"{self.config.api_url}/health", timeout=5)
                if r.status_code == 200:
                    system_ok = True
                r2 = httpx.get(f"{self.config.api_url}/metrics", timeout=5)
                if r2.status_code == 200:
                    metrics_ok = True
                if system_ok and metrics_ok:
                    break
            except Exception:
                pass
            time.sleep(2)

        if system_ok:
            self.log("API continues functioning without Prometheus")
        else:
            self.errors.append("API health check failed after Prometheus stopped")

        if metrics_ok:
            self.log("Metrics endpoint still serves data")
        else:
            self.errors.append("Metrics endpoint unavailable after Prometheus stopped")

        self.log(f"Holding Prometheus down for {self.config.disruption_duration}s")
        time.sleep(self.config.disruption_duration)

    def verify_recovery(self) -> bool:
        self.log(f"Starting Prometheus container: {self.config.container_names['prometheus']}")
        code, output = self._docker("start", self.config.container_names["prometheus"])
        if code != 0:
            self.errors.append(f"docker start prometheus failed: {output}")

        self.log("Waiting for Prometheus to initialize")
        time.sleep(5)
        return self._wait_for_healthy()
