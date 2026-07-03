import os
from dataclasses import dataclass, field


@dataclass
class ChaosConfig:
    api_url: str = os.getenv("TECHFLOW_API_URL", "http://localhost:8000")
    api_key: str = os.getenv("TECHFLOW_API_KEY", "test-key-12345")
    docker_network: str = os.getenv("TECHFLOW_DOCKER_NETWORK", "techflow_default")
    compose_project: str = os.getenv("TECHFLOW_COMPOSE_PROJECT", "")

    health_timeout: int = int(os.getenv("CHAOS_HEALTH_TIMEOUT", "60"))
    health_interval: float = float(os.getenv("CHAOS_HEALTH_INTERVAL", "2"))

    recovery_timeout: int = int(os.getenv("CHAOS_RECOVERY_TIMEOUT", "120"))
    recovery_interval: float = float(os.getenv("CHAOS_RECOVERY_INTERVAL", "2"))

    disruption_duration: int = int(os.getenv("CHAOS_DISRUPTION_DURATION", "30"))

    container_names: dict = field(default_factory=lambda: {
        "api": "techflow-api",
        "worker": "techflow-worker",
        "postgres": "techflow-postgres",
        "kafka": "techflow-kafka",
        "zookeeper": "techflow-zookeeper",
        "prometheus": "techflow-prometheus",
        "grafana": "techflow-grafana",
    })

    @property
    def is_production(self) -> bool:
        env = os.getenv("TECHFLOW_ENV", "").lower()
        return env in ("production", "prod")

    @property
    def compose_flags(self) -> list[str]:
        flags = []
        if self.compose_project:
            flags.extend(["-p", self.compose_project])
        return flags
