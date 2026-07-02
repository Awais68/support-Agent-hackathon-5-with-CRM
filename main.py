"""Entry point for TechFlow CRM Digital FTE - supports API and worker modes."""

import os
import sys
import asyncio
import uvicorn


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
    mode = os.getenv("RUN_MODE", "api").lower()

    print(f"TechFlow CRM Digital FTE - Starting in {mode} mode")

    if mode == "worker":
        asyncio.run(run_worker_mode())
    else:
        run_api_mode()


if __name__ == "__main__":
    main()
