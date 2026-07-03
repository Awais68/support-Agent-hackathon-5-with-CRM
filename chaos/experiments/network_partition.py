import time

from chaos.experiments.base import ChaosExperiment


class NetworkPartitionExperiment(ChaosExperiment):
    name = "05_network_partition"

    def inject(self) -> None:
        worker = self.config.container_names["worker"]
        network = self.config.docker_network
        self.log(f"Disconnecting {worker} from {network}")
        code, output = self._run_cmd([
            "docker", "network", "disconnect", network, worker,
        ])
        if code != 0:
            self.errors.append(f"docker network disconnect failed: {output}")

        self.log(f"Holding partition for {self.config.disruption_duration}s")
        time.sleep(self.config.disruption_duration)

    def verify_recovery(self) -> bool:
        worker = self.config.container_names["worker"]
        network = self.config.docker_network
        self.log(f"Reconnecting {worker} to {network}")
        code, output = self._run_cmd([
            "docker", "network", "connect", network, worker,
        ])
        if code != 0:
            self.errors.append(f"docker network connect failed: {output}")

        self.log("Waiting for worker to reconnect and consumer rebalance")
        time.sleep(10)
        return self._wait_for_healthy()
