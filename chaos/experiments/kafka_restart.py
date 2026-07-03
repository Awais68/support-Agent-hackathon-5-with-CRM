import time

from chaos.experiments.base import ChaosExperiment


class KafkaRestartExperiment(ChaosExperiment):
    name = "02_kafka_restart"

    def inject(self) -> None:
        self.log(f"Restarting Kafka container: {self.config.container_names['kafka']}")
        code, output = self._docker("restart", self.config.container_names["kafka"])
        if code != 0:
            self.errors.append(f"docker restart kafka failed: {output}")

    def verify_recovery(self) -> bool:
        self.log("Waiting for Kafka to become healthy and consumer rebalance")
        time.sleep(5)
        kafka_container = self.config.container_names["kafka"]
        deadline = time.time() + self.config.recovery_timeout
        while time.time() < deadline:
            code, output = self._docker("exec", kafka_container, "kafka-broker-api-versions", "--bootstrap-server", "localhost:9092")
            if code == 0:
                break
            time.sleep(3)
        return self._wait_for_healthy(timeout=self.config.recovery_timeout)
