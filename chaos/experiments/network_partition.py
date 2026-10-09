import time

from chaos.experiments.base import ChaosExperiment

# Run inside the worker: exit 0 when the Kafka broker accepts a TCP connection.
_REACH_KAFKA = (
    "import os, socket, sys\n"
    "host, _, port = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092')"
    ".split(',')[0].rpartition(':')\n"
    "try:\n"
    "    socket.create_connection((host, int(port)), timeout=3).close()\n"
    "except OSError:\n"
    "    sys.exit(1)\n"
)


class NetworkPartitionExperiment(ChaosExperiment):
    name = "05_network_partition"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Resolved once, before the disconnect: afterwards the worker has no
        # network to read it from, and the reconnect used to get "".
        self._net = ""

    def _network(self) -> str:
        if self.config.docker_network:
            return self.config.docker_network
        # Compose names the network <project>_techflow; take it from the worker.
        code, output = self._docker(
            "inspect",
            self.config.container_names["worker"],
            "--format",
            "{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}",
        )
        networks = output.split() if code == 0 else []
        return networks[0] if networks else ""

    def _worker_reaches_kafka(self) -> bool:
        code, _ = self._docker(
            "exec", self.config.container_names["worker"], "python", "-c", _REACH_KAFKA
        )
        return code == 0

    def inject(self) -> None:
        worker = self.config.container_names["worker"]
        self._net = self._network()
        if not self._net:
            self.errors.append(f"could not find the network of {worker}")
            return
        self.log(f"Disconnecting {worker} from {self._net}")
        code, output = self._run_cmd(["docker", "network", "disconnect", self._net, worker])
        if code != 0:
            self.errors.append(f"docker network disconnect failed: {output}")
            return
        if self._worker_reaches_kafka():
            self.errors.append("worker still reaches Kafka after the disconnect")

        self.log(f"Holding partition for {self.config.disruption_duration}s")
        time.sleep(self.config.disruption_duration)

    def verify_recovery(self) -> bool:
        worker = self.config.container_names["worker"]
        if not self._net:
            return False
        self.log(f"Reconnecting {worker} to {self._net}")
        code, output = self._run_cmd(["docker", "network", "connect", self._net, worker])
        if code != 0:
            self.errors.append(f"docker network connect failed: {output}")
            return False

        self.log("Waiting for worker to reconnect and consumer rebalance")
        time.sleep(10)
        if not self._worker_reaches_kafka():
            self.errors.append("worker cannot reach Kafka after the reconnect")
            return False
        return self._wait_for_system_and_worker()
