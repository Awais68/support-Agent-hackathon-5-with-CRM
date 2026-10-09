import time

from chaos.experiments.base import ChaosExperiment


class WorkerKillExperiment(ChaosExperiment):
    name = "04_worker_kill"

    def inject(self) -> None:
        self.log(f"Crashing the worker process in {self.config.container_names['worker']}")
        code, output = self._crash(self.config.container_names["worker"])
        if code != 0:
            self.errors.append(f"crashing the worker process failed: {output}")

    def verify_recovery(self) -> bool:
        self.log("Waiting for Docker restart policy to restart worker container")
        time.sleep(5)
        return self._wait_for_system_and_worker()
