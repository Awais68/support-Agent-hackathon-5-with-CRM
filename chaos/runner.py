#!/usr/bin/env python3
"""
Chaos experiment runner for TechFlow CRM.

Usage:
    python -m chaos.runner [--dry-run] [--experiment 01] [--all]

Safety:
    - Will NOT run if TECHFLOW_ENV=production
    - Requires explicit confirmation (TECHFLOW_CONFIRM_CHAOS=yes or interactive)
    - Use --dry-run to preview without making changes
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from chaos.config import ChaosConfig
from chaos.safety import (
    SafetyCheckError,
    check_production,
    dry_run_mode,
    require_confirmation,
)

EXPERIMENTS = {
    "01": ("chaos.experiments.api_pod_deletion", "ApiPodDeletionExperiment"),
    "02": ("chaos.experiments.kafka_restart", "KafkaRestartExperiment"),
    "03": ("chaos.experiments.postgres_outage", "PostgresOutageExperiment"),
    "04": ("chaos.experiments.worker_kill", "WorkerKillExperiment"),
    "05": ("chaos.experiments.network_partition", "NetworkPartitionExperiment"),
    "06": ("chaos.experiments.metrics_pipeline", "MetricsPipelineExperiment"),
}


def import_experiment(module_path: str, class_name: str):
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


def run_experiment(experiment_id: str, config: ChaosConfig, is_dry_run: bool):
    if experiment_id not in EXPERIMENTS:
        print(f"Unknown experiment: {experiment_id}")
        return None

    module_path, class_name = EXPERIMENTS[experiment_id]
    cls = import_experiment(module_path, class_name)
    exp = cls(config)

    print(f"\n{'='*60}")
    print(f"Experiment: {exp.name}")
    print(f"{'='*60}")

    if is_dry_run:
        print(f"[DRY-RUN] Would execute: {exp.name}")
        print(f"[DRY-RUN] Failure injection: {exp.__class__.__name__}.inject()")
        print(f"[DRY-RUN] Recovery verification: {exp.__class__.__name__}.verify_recovery()")
        return {
            "experiment_name": exp.name,
            "status": "dry-run",
            "recovery_time_seconds": None,
            "errors_observed": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "details": {"note": "Dry run — no changes made"},
        }

    result = exp.run()
    print(f"\nResult: {result.status}")
    if result.recovery_time_seconds is not None:
        print(f"Recovery time: {result.recovery_time_seconds}s")
    if result.errors_observed:
        print(f"Errors: {result.errors_observed}")
    print(result.to_json())
    return result.to_dict()


def main():
    parser = argparse.ArgumentParser(description="TechFlow Chaos Experiment Runner")
    parser.add_argument(
        "--experiment", "-e",
        choices=list(EXPERIMENTS.keys()) + ["all"],
        default="all",
        help="Experiment ID to run (01-06), or 'all'",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Preview actions without making changes",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt (sets TECHFLOW_CONFIRM_CHAOS=yes)",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        help="Write results JSON to file",
    )
    args = parser.parse_args()

    config = ChaosConfig()

    is_dry_run = args.dry_run or dry_run_mode()
    if args.yes:
        import os
        os.environ["TECHFLOW_CONFIRM_CHAOS"] = "yes"

    try:
        check_production(config)
    except SafetyCheckError as e:
        print(f"SAFETY BLOCKED: {e}")
        sys.exit(1)

    if not is_dry_run:
        require_confirmation()

    if args.experiment == "all":
        ids = sorted(EXPERIMENTS.keys())
    else:
        ids = [args.experiment]

    results = []
    for eid in ids:
        result = run_experiment(eid, config, is_dry_run)
        if result:
            results.append(result)
        time.sleep(2)

    summary = {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
        "total": len(results),
        "passed": sum(1 for r in results if r["status"] == "passed"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "dry_run": sum(1 for r in results if r["status"] == "dry-run"),
        "results": results,
    }

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Total: {summary['total']}")
    print(f"Passed: {summary['passed']}")
    print(f"Failed: {summary['failed']}")
    print(f"Errors: {summary['errors']}")
    print(f"Dry-run: {summary['dry_run']}")

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, indent=2, default=str))
        print(f"\nResults written to {output_path}")

    if summary["failed"] > 0 or summary["errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
