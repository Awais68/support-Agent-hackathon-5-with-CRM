import os
import sys


class SafetyCheckError(Exception):
    pass


def check_production(cfg) -> None:
    if cfg.is_production:
        raise SafetyCheckError(
            "REFUSED: TECHFLOW_ENV is set to 'production'. "
            "Chaos experiments must never target production. "
            "Set TECHFLOW_ENV=staging or unset it to proceed."
        )

    kube_context = os.getenv("KUBECONFIG_CONTEXT", "")
    if "prod" in kube_context.lower() or "production" in kube_context.lower():
        raise SafetyCheckError(
            f"REFUSED: KUBECONFIG_CONTEXT '{kube_context}' looks like production. "
            "Chaos experiments are not allowed on production clusters."
        )


def require_confirmation() -> None:
    confirm = os.getenv("TECHFLOW_CONFIRM_CHAOS", "").lower()
    if confirm not in ("yes", "1", "true"):
        print("SAFETY: Chaos experiments require explicit confirmation.")
        print("Set TECHFLOW_CONFIRM_CHAOS=yes to proceed.")
        print("Run with --dry-run first to see what would happen.\n")
        choice = input("Type 'yes' to continue, anything else to abort: ").strip().lower()
        if choice != "yes":
            print("Aborted by user.")
            sys.exit(0)


def dry_run_mode() -> bool:
    return "--dry-run" in sys.argv or os.getenv("CHAOS_DRY_RUN", "").lower() in ("1", "true")
