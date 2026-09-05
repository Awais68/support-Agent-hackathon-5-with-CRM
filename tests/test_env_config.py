"""Tests for .env layering.

The loader used to pick ``.env.<ENVIRONMENT>`` *or* ``.env``, never both.
Because ``.env.development`` exists in this repo, anything defined only in
``.env`` — GEMINI_API_KEY among them — was invisible to main.py and the worker.
"""

import os

import pytest

from env_config import load_environment


@pytest.fixture
def env_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for var in ("SHARED_ONLY", "OVERRIDDEN", "FROM_SHELL", "ENVIRONMENT"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_env_specific_layers_on_top_of_env(env_dir, monkeypatch):
    (env_dir / ".env").write_text("SHARED_ONLY=from-env\nOVERRIDDEN=from-env\n")
    (env_dir / ".env.development").write_text("OVERRIDDEN=from-development\n")

    loaded = load_environment()

    assert loaded == [".env.development", ".env"]
    # The shared fallback is still applied, not skipped.
    assert os.getenv("SHARED_ONLY") == "from-env"
    # The environment-specific file still wins where both define a key.
    assert os.getenv("OVERRIDDEN") == "from-development"


def test_shell_environment_beats_every_file(env_dir, monkeypatch):
    (env_dir / ".env").write_text("FROM_SHELL=from-env\n")
    (env_dir / ".env.development").write_text("FROM_SHELL=from-development\n")
    monkeypatch.setenv("FROM_SHELL", "from-shell")

    load_environment()

    assert os.getenv("FROM_SHELL") == "from-shell"


def test_environment_selects_the_specific_file(env_dir, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    (env_dir / ".env").write_text("OVERRIDDEN=from-env\n")
    (env_dir / ".env.production").write_text("OVERRIDDEN=from-production\n")
    (env_dir / ".env.development").write_text("OVERRIDDEN=from-development\n")

    loaded = load_environment()

    assert loaded == [".env.production", ".env"]
    assert os.getenv("OVERRIDDEN") == "from-production"


def test_missing_files_are_skipped(env_dir):
    assert load_environment() == []
