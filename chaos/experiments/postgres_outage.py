import time

from chaos.experiments.base import ChaosExperiment


class PostgresOutageExperiment(ChaosExperiment):
    name = "03_postgres_outage"

    def inject(self) -> None:
        self.log(f"Stopping Postgres container: {self.config.container_names['postgres']}")
        code, output = self._docker("stop", self.config.container_names["postgres"])
        if code != 0:
            self.errors.append(f"docker stop postgres failed: {output}")

        self.log("Verifying API enters degraded mode")
        import httpx
        deadline = time.time() + 15
        degraded_detected = False
        while time.time() < deadline:
            try:
                r = httpx.get(f"{self.config.api_url}/health", timeout=5)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("status") == "degraded" and data.get("db") == "error":
                        degraded_detected = True
                        break
            except Exception:
                pass
            time.sleep(2)

        if degraded_detected:
            self.log("API correctly entered degraded mode (db:error)")
        else:
            self.errors.append("API did not report degraded mode after Postgres outage")
            self.log("WARNING: API did not enter degraded mode as expected")

        self.log(f"Holding Postgres down for {self.config.disruption_duration}s")
        time.sleep(self.config.disruption_duration)

    def verify_recovery(self) -> bool:
        self.log(f"Starting Postgres container: {self.config.container_names['postgres']}")
        code, output = self._docker("start", self.config.container_names["postgres"])
        if code != 0:
            self.errors.append(f"docker start postgres failed: {output}")

        self.log("Waiting for Postgres to become healthy")
        pg_container = self.config.container_names["postgres"]
        deadline = time.time() + 30
        while time.time() < deadline:
            code, output = self._docker("exec", pg_container, "pg_isready", "-U", "techflow")
            if code == 0:
                break
            time.sleep(2)

        self.log("Waiting for API to detect Postgres recovery")
        return self._wait_for_healthy()
