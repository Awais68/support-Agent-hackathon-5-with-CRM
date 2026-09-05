"""Single place that decides which .env files a process loads.

Every entrypoint (main.py, the worker, uvicorn importing api.main) used to do
this slightly differently, and two of them loaded ``.env.<ENVIRONMENT>``
*instead of* ``.env`` rather than on top of it. Because ``.env.development``
exists in this repo, anything set only in ``.env`` — GEMINI_API_KEY among
them — was invisible to those processes.
"""

import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv


def load_environment(verbose: bool = False) -> List[str]:
    """Load .env files in precedence order and return the files applied.

    Priority (highest to lowest):
      1. Shell environment variables (already set, never overridden)
      2. ``.env.<ENVIRONMENT>``  (e.g. .env.development, .env.production)
      3. ``.env``  (shared fallback for anything the specific file omits)

    ``load_dotenv`` does not overwrite values already present, so loading the
    environment-specific file first and ``.env`` second yields exactly that
    order.
    """
    env = os.getenv("ENVIRONMENT", "development")
    loaded: List[str] = []

    for path in (Path(f".env.{env}"), Path(".env")):
        if path.exists():
            load_dotenv(path)
            loaded.append(str(path))

    if verbose:
        if loaded:
            print(f"Loaded environment: {', '.join(loaded)}")
        else:
            print("Warning: No .env file found — relying on shell environment")

    return loaded
