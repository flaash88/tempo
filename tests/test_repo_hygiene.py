"""The non-negotiable rule: no health data and no secrets in the repository.

These checks belong in the test suite rather than in a review checklist —
a .gitignore that quietly loses an entry is exactly the failure mode that
puts a heart rate file into a public repository.
"""

from __future__ import annotations

import ast
import re
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


# --- configuration, documented in both places --------------------------

# Every variable the application reads has to appear in .env.example and
# in the README's table. Keeping that true by hand lasted exactly as long
# as it took to add the next one, which is why it is checked here.
ENV_READERS = frozenset({"_env_str", "_env_bool", "_env_float", "_env_int"})

# Names belonging to Tempo's own configuration. The README mentions other
# capitalised words — ``API_KEY`` is the literal basic-auth username, not a
# variable — so the reverse check is scoped to these prefixes.
CONFIG_PREFIXES = ("TEMPO_", "INTERVALS_", "ANTHROPIC_", "GARMIN_")

CONFIG_MODULE = REPO_ROOT / "tempo" / "config.py"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
README = REPO_ROOT / "README.md"


def _first_string_argument(call: ast.Call) -> str | None:
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def variables_the_code_reads() -> set[str]:
    """The names passed to the environment readers, from the syntax tree.

    Parsed rather than grepped: a name built by string concatenation would
    slip past a regular expression, and this check is only worth having if
    it cannot be fooled by the next refactor.
    """
    tree = ast.parse(CONFIG_MODULE.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _env_variable_name(node)
        if name and name.isupper():
            found.add(name)
    return found


def _env_variable_name(call: ast.Call) -> str | None:
    """The variable one call reads, whether through a helper or os.environ."""
    function = call.func
    if isinstance(function, ast.Name):
        if function.id not in ENV_READERS:
            return None
        return _first_string_argument(call)
    if (
        isinstance(function, ast.Attribute)
        and function.attr == "get"
        and isinstance(function.value, ast.Attribute)
        and function.value.attr == "environ"
    ):
        return _first_string_argument(call)
    return None


def variables_in_env_example() -> set[str]:
    return {
        match.group(1)
        for match in re.finditer(
            r"^([A-Z][A-Z0-9_]*)=",
            ENV_EXAMPLE.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    }


def variables_in_readme() -> set[str]:
    return set(
        re.findall(r"`([A-Z][A-Z0-9_]{2,})`", README.read_text(encoding="utf-8"))
    )


def test_the_scan_finds_the_configuration_at_all() -> None:
    """If this breaks, the three checks below are checking nothing."""
    read = variables_the_code_reads()

    assert "INTERVALS_API_KEY" in read
    assert "TEMPO_MONTHLY_BUDGET_EUR" in read
    assert len(read) >= 12


def test_every_variable_the_code_reads_is_in_env_example() -> None:
    missing = sorted(variables_the_code_reads() - variables_in_env_example())

    assert missing == [], f".env.example is missing: {missing}"


def test_every_variable_the_code_reads_is_in_the_readme() -> None:
    missing = sorted(variables_the_code_reads() - variables_in_readme())

    assert missing == [], f"the README is missing: {missing}"


def test_neither_document_carries_a_variable_the_code_dropped() -> None:
    """The other direction: a renamed variable leaves a lie behind."""
    read = variables_the_code_reads()

    stale_example = sorted(variables_in_env_example() - read)
    stale_readme = sorted(
        name
        for name in variables_in_readme() - read
        if name.startswith(CONFIG_PREFIXES)
    )

    assert stale_example == [], (
        f".env.example documents what is not read: {stale_example}"
    )
    assert stale_readme == [], f"the README documents what is not read: {stale_readme}"
