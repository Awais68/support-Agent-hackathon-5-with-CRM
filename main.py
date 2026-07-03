"""Entry point for TechFlow CRM Digital FTE - supports API and worker modes."""

import os
import sys
import asyncio
from pathlib import Path

from dotenv import load_dotenv
import uvicorn


def load_environment():
    """Load environment-specific .env file based on ENVIRONMENT setting.

    Priority (highest to lowest):
      1. Shell environment variables (already set, not overridden)
      2. .env.<ENVIRONMENT>  (e.g. .env.development, .env.production)
      3. .env  (fallback with common defaults)
    """
    env = os.getenv("ENVIRONMENT", "development")
    env_specific = Path(f".env.{env}")
    env_default = Path(".env")

    if env_specific.exists():
        load_dotenv(env_specific)
        print(f"Loaded environment: .env.{env}")
    elif env_default.exists():
        load_dotenv(env_default)
        print(f"Loaded environment: .env (fallback)")
    else:
        print("Warning: No .env file found — relying on shell environment")


def run_api_mode():
    """Run in API server mode."""
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", 8000))
    reload = os.getenv("API_RELOAD", "false").lower() == "true"

    print(f"🚀 Starting API server on {host}:{port}")
    uvicorn.run(
        "api.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


async def run_worker_mode():
    """Run in background worker mode."""
    from workers.message_processor import main as worker_main

    print("🤖 Starting message processor worker")
    await worker_main()


def main():
    """Main entry point - routes to API or worker mode."""
    load_environment()
    mode = os.getenv("RUN_MODE", "api").lower()

    print(f"TechFlow CRM Digital FTE - Starting in {mode} mode")

    if mode == "worker":
        asyncio.run(run_worker_mode())
    else:
        run_api_mode()


if __name__ == "__main__":
    main()
