import time

from chaos.experiments.base import ChaosExperiment


class WorkerKillExperiment(ChaosExperiment):
    name = "04_worker_kill"

    def inject(self) -> None:
        self.log(f"Killing worker container: {self.config.container_names['worker']}")
        code, output = self._docker("kill", self.config.container_names["worker"])
        if code != 0:
            self.errors.append(f"docker kill worker failed: {output}")

    def verify_recovery(self) -> bool:
        self.log("Waiting for Docker restart policy to restart worker container")
        time.sleep(5)
        return self._wait_for_healthy()
