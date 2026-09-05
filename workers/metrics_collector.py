"""Metrics collector worker for TechFlow CRM Digital FTE."""
import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Dict

import asyncpg
import structlog

from kafka_client import KafkaProducerClient
from database import queries as db
from exceptions import sanitize_error_message

logger = structlog.get_logger(__name__)


def _to_naive_utc(dt: datetime) -> datetime:
    """Strip tzinfo for DB columns that are TIMESTAMP WITHOUT TIME ZONE."""
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


class MetricsCollector:
    """Collects and reports metrics about the support system."""

    def __init__(self, db_pool: asyncpg.Pool, kafka_producer: KafkaProducerClient):
        self.db_pool = db_pool
        self.kafka_producer = kafka_producer
        self.collection_interval = 300  # 5 minutes

    async def collect_metrics(self) -> Dict[str, Any]:
        """Collect all metrics."""
        try:
            metrics = {}

            ticket_metrics = await self._get_ticket_metrics()
            metrics.update(ticket_metrics)

            resolution_metrics = await self._get_resolution_metrics()
            metrics.update(resolution_metrics)

            agent_metrics = await self._get_agent_metrics()
            metrics.update(agent_metrics)

            channel_metrics = await self._get_channel_metrics()
            metrics.update(channel_metrics)

            logger.info(
                "Metrics collected",
                metric_count=len(metrics),
                metrics=list(metrics.keys()),
            )
            return metrics
        except Exception as e:
            logger.error("Error collecting metrics", error=sanitize_error_message(str(e)))
            return {}

    async def _get_ticket_metrics(self) -> Dict[str, float]:
        """Get ticket-related metrics."""
        try:
            async with self.db_pool.acquire() as conn:
                total = await conn.fetchval("SELECT COUNT(*) FROM tickets")

                status_counts = await conn.fetch(
                    "SELECT status, COUNT(*) as count FROM tickets GROUP BY status"
                )

                one_hour_ago = _to_naive_utc(datetime.now(UTC) - timedelta(hours=1))
                created_last_hour = await conn.fetchval(
                    "SELECT COUNT(*) FROM tickets WHERE created_at >= $1",
                    one_hour_ago,
                )

                start_of_today = _to_naive_utc(
                    datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
                )
                created_today = await conn.fetchval(
                    "SELECT COUNT(*) FROM tickets WHERE created_at >= $1",
                    start_of_today,
                )

                metrics = {
                    "tickets_total": float(total),
                    "tickets_open": float(
                        next((c["count"] for c in status_counts if c["status"] == "open"), 0)
                    ),
                    "tickets_in_progress": float(
                        next((c["count"] for c in status_counts if c["status"] == "in_progress"), 0)
                    ),
                    "tickets_resolved": float(
                        next((c["count"] for c in status_counts if c["status"] == "resolved"), 0)
                    ),
                    "tickets_escalated": float(
                        next((c["count"] for c in status_counts if c["status"] == "escalated"), 0)
                    ),
                    "tickets_created_last_hour": float(created_last_hour),
                    "tickets_created_today": float(created_today),
                }
                return metrics
        except Exception as e:
            logger.error("Error getting ticket metrics", error=sanitize_error_message(str(e)))
            return {}

    async def _get_resolution_metrics(self) -> Dict[str, float]:
        """Get resolution-related metrics."""
        try:
            async with self.db_pool.acquire() as conn:
                avg_resolution_hours = await conn.fetchval(
                    """
                    SELECT AVG(EXTRACT(EPOCH FROM (resolved_at - created_at))/3600)
                    FROM tickets
                    WHERE resolved_at IS NOT NULL
                    """
                )
                median_resolution_hours = await conn.fetchval(
                    """
                    SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (
                        ORDER BY EXTRACT(EPOCH FROM (resolved_at - created_at))/3600
                    )
                    FROM tickets
                    WHERE resolved_at IS NOT NULL
                    """
                )
                total_tickets = await conn.fetchval("SELECT COUNT(*) FROM tickets")
                escalated_tickets = await conn.fetchval(
                    "SELECT COUNT(*) FROM tickets WHERE status = 'escalated'"
                )
                escalation_rate = (
                    (escalated_tickets / total_tickets * 100) if total_tickets > 0 else 0
                )

                metrics = {
                    "avg_resolution_hours": float(avg_resolution_hours) if avg_resolution_hours else 0,
                    "median_resolution_hours": float(median_resolution_hours)
                    if median_resolution_hours
                    else 0,
                    "escalation_rate_percent": float(escalation_rate),
                }
                return metrics
        except Exception as e:
            logger.error("Error getting resolution metrics", error=sanitize_error_message(str(e)))
            return {}

    async def _get_agent_metrics(self) -> Dict[str, float]:
        """Get agent performance metrics."""
        try:
            async with self.db_pool.acquire() as conn:
                total_runs = await conn.fetchval("SELECT COUNT(*) FROM agent_runs")
                successful_runs = await conn.fetchval(
                    "SELECT COUNT(*) FROM agent_runs WHERE status = 'completed'"
                )
                failed_runs = await conn.fetchval(
                    "SELECT COUNT(*) FROM agent_runs WHERE status = 'failed'"
                )
                avg_tokens = await conn.fetchval(
                    "SELECT AVG(tokens_used) FROM agent_runs WHERE tokens_used > 0"
                )
                avg_duration_ms = await conn.fetchval(
                    "SELECT AVG(duration_ms) FROM agent_runs WHERE duration_ms > 0"
                )

                success_rate = (
                    (successful_runs / total_runs * 100) if total_runs > 0 else 0
                )

                metrics = {
                    "agent_runs_total": float(total_runs),
                    "agent_runs_successful": float(successful_runs),
                    "agent_runs_failed": float(failed_runs),
                    "agent_success_rate_percent": float(success_rate),
                    "agent_avg_tokens": float(avg_tokens) if avg_tokens else 0,
                    "agent_avg_duration_ms": float(avg_duration_ms) if avg_duration_ms else 0,
                }
                return metrics
        except Exception as e:
            logger.error("Error getting agent metrics", error=sanitize_error_message(str(e)))
            return {}

    async def _get_channel_metrics(self) -> Dict[str, float]:
        """Get channel usage metrics."""
        try:
            async with self.db_pool.acquire() as conn:
                channel_counts = await conn.fetch(
                    "SELECT channel, COUNT(*) as count FROM tickets GROUP BY channel"
                )
                metrics = {
                    f"tickets_via_{channel['channel']}": float(channel["count"])
                    for channel in channel_counts
                }
                return metrics
        except Exception as e:
            logger.error("Error getting channel metrics", error=sanitize_error_message(str(e)))
            return {}

    async def report_metrics(self, metrics: Dict[str, Any]) -> None:
        """Report metrics to Kafka and persist to DB."""
        try:
            for metric_name, metric_value in metrics.items():
                # A Kafka failure for one metric must not abort the whole batch
                # (and must not skip DB persistence for the remaining metrics).
                try:
                    await self.kafka_producer.send_message(
                        "metrics.events",
                        {
                            "metric_name": metric_name,
                            "metric_value": metric_value,
                            "timestamp": datetime.now(UTC).isoformat(),
                            "metric_type": "gauge",
                        },
                        key=metric_name,
                    )
                except Exception as kafka_err:
                    logger.error(
                        "Error publishing metric to Kafka",
                        metric_name=metric_name,
                        error=sanitize_error_message(str(kafka_err)),
                    )

                safe_value = metric_value
                if isinstance(safe_value, (dict, list)):
                    safe_value = json.dumps(safe_value)

                try:
                    await db.record_metric(
                        self.db_pool,
                        metric_name=metric_name,
                        metric_value=safe_value,
                        metric_type="gauge",
                    )
                except Exception as db_err:
                    logger.error(
                        "Error persisting metric to DB",
                        metric_name=metric_name,
                        error=sanitize_error_message(str(db_err)),
                    )

            logger.info(
                "Metrics reported",
                metric_count=len(metrics),
            )
        except Exception as e:
            logger.error("Error reporting metrics", error=sanitize_error_message(str(e)))


async def run_metrics_collector(
    db_pool: asyncpg.Pool,
    kafka_producer: KafkaProducerClient,
    collection_interval: int = 300,
) -> None:
    """Run metrics collection loop."""
    collector = MetricsCollector(db_pool, kafka_producer)
    logger.info(
        "Starting metrics collector",
        collection_interval=collection_interval,
    )

    while True:
        try:
            metrics = await collector.collect_metrics()
            await collector.report_metrics(metrics)
        except Exception as e:
            logger.error("Error in metrics collection loop", error=sanitize_error_message(str(e)))
        await asyncio.sleep(collection_interval)
