import time

from chaos.experiments.base import ChaosExperiment


class ApiPodDeletionExperiment(ChaosExperiment):
    name = "01_api_pod_deletion"

    def inject(self) -> None:
        self.log(f"Killing API container: {self.config.container_names['api']}")
        code, output = self._docker("kill", self.config.container_names["api"])
        if code != 0:
            self.errors.append(f"docker kill failed: {output}")

    def verify_recovery(self) -> bool:
        self.log("Waiting for Docker restart policy to restart API container")
        time.sleep(3)
        return self._wait_for_healthy()
