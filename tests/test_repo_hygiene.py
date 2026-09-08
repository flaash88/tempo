"""The non-negotiable rule: no health data and no secrets in the repository.

These checks belong in the test suite rather than in a review checklist —
a .gitignore that quietly loses an entry is exactly the failure mode that
puts a heart rate file into a public repository.
"""

from __future__ import annotations

import subprocess

import pytest

from tests import REPO_ROOT

REQUIRED_IGNORES = (
    ".env",
    "data/tempo.db",
    "data/fit/2026-01-15.fit",
    "activity.fit",
    "tempo.db",
    "tempo.sqlite3",
    ".garth/session.json",
    ".garminconnect/token",
    "tokens.json",
    "tempo/__pycache__/cli.cpython-312.pyc",
    ".venv/bin/python",
    "web/node_modules/react/index.js",
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )


@pytest.mark.parametrize("path", REQUIRED_IGNORES)
def test_sensitive_paths_are_ignored(path: str) -> None:
    result = _git("check-ignore", "--quiet", path)

    assert result.returncode == 0, f"{path} is not covered by .gitignore"


def test_env_example_is_not_ignored() -> None:
    """The template must stay in the repository — it documents the setup."""
    result = _git("check-ignore", "--quiet", ".env.example")

    assert result.returncode == 1


def _is_sensitive(name: str) -> bool:
    if name == ".env.example":
        return False
    if name == ".env" or name.startswith((".env.", "data/")):
        return True
    return name.endswith((".fit", ".db", ".sqlite", ".sqlite3"))


def test_no_sensitive_file_is_tracked() -> None:
    tracked = _git("ls-files").stdout.splitlines()

    offenders = [name for name in tracked if _is_sensitive(name)]

    assert offenders == []
