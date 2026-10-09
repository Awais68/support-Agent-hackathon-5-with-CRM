"""S5: secrets and scratch files never reach a Docker build context.

The API image shipped `.env.development`, `.env.development.bak`,
`credentials.json` and `.kilo/`. Docker itself evaluates `.dockerignore`
here: a throwaway build copies the context and lists it. Plant decoy files
of every sensitive kind in a temporary copy of the ignore rules, so the
test does not depend on what happens to be in the developer's checkout.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent

SENSITIVE = [
    ".env",
    ".env.development",
    ".env.development.bak",
    ".env.production",
    ".env.local",
    "config.bak",
    "credentials.json",
    "client_secret.json",
    "gmail_credentials.json",
    "gmail_token.json",
    "gmail_token.pickle",
    "dump.sql",
    "service.pem",
    ".kilo/session.json",
    ".claude/settings.local.json",
    "scratch/notes.txt",
    "scratchpad/notes.txt",
    "tmp/dump.sql",
    ".coverage",
    "nested/.env",
    "nested/creds.bak",
]
# Real code with look-alike names must still be copied.
KEPT = [
    "api/main.py",
    "channels/gmail_handler.py",
    "scripts/gmail_auth.py",
    "database/migrations/001_initial.sql",
]

LIST_CONTEXT = "FROM busybox:1.36\nCOPY . /ctx\nRUN cd /ctx && find . -type f | sort\n"


def _context_files(dockerignore: Path, tmp_path: Path) -> set[str]:
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    shutil.copy(dockerignore, ctx / ".dockerignore")
    for rel in SENSITIVE + KEPT:
        (ctx / rel).parent.mkdir(parents=True, exist_ok=True)
        (ctx / rel).write_text("decoy\n")
    try:
        proc = subprocess.run(
            ["docker", "build", "--no-cache", "--progress=plain", "-f", "-", str(ctx)],
            input=LIST_CONTEXT,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        pytest.skip(f"docker unavailable: {e}")
    if proc.returncode != 0:
        pytest.skip(f"docker build failed: {proc.stderr[-400:]}")
    # Plain progress lines look like "#7 0.136 ./api/main.py".
    files = set()
    for line in proc.stderr.splitlines():
        part = line.rsplit(" ", 1)[-1].strip()
        if part.startswith("./"):
            files.add(part[2:])
    assert files, "could not read the context listing from docker build output"
    return files


@pytest.mark.parametrize("dockerignore", [".dockerignore", "web-form/.dockerignore"])
def test_sensitive_files_are_not_in_build_context(dockerignore, tmp_path):
    files = _context_files(ROOT / dockerignore, tmp_path)
    leaked = sorted(f for f in SENSITIVE if f in files)
    assert not leaked, f"{dockerignore} lets these into the image: {leaked}"
    missing = sorted(f for f in KEPT if f not in files)
    assert not missing, f"{dockerignore} drops needed files: {missing}"
