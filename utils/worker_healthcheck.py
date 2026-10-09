"""Worker probe: exit 0 only if every heartbeat file is fresh (AUDIT N6/C2).

Usage: python -m utils.worker_healthcheck --max-age SECONDS
"""

import argparse
import sys
import time

from utils.heartbeat import heartbeat_dir


def check(max_age: float) -> list[str]:
    """Return the problems found; an empty list means healthy."""
    directory = heartbeat_dir()
    if not directory.is_dir():
        return [f"no heartbeat directory {directory}"]
    files = [p for p in directory.iterdir() if p.is_file()]
    if not files:
        return ["no heartbeats yet"]
    now = time.time()
    problems = []
    for path in sorted(files):
        age = now - path.stat().st_mtime
        if age > max_age:
            problems.append(f"{path.name}: last heartbeat {age:.0f}s ago")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-age", type=float, default=60.0)
    args = parser.parse_args(argv)
    problems = check(args.max_age)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
