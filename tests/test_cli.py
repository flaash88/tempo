"""The CLI skeleton: init works, the later phases say so plainly."""

from __future__ import annotations

import getpass
from pathlib import Path

import pytest
from sqlalchemy import inspect

from tempo.api.auth import verify_password
from tempo.cli import build_parser, main
from tempo.config import Settings
from tempo.db.migrate import current_revision, head_revision
from tempo.db.session import create_db_engine


def test_init_creates_the_data_layout_and_the_schema(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["init"])

    assert exit_code == 0
    assert settings.data_dir.is_dir()
    assert settings.fit_dir.is_dir()

    engine = create_db_engine(settings.database_url)
    try:
        assert current_revision(engine) == head_revision()
        assert "activity" in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    assert f"Schema-Revision:  {head_revision()}" in capsys.readouterr().out


def test_init_is_repeatable(settings: Settings) -> None:
    assert main(["init"]) == 0
    assert main(["init"]) == 0


@pytest.mark.parametrize("argv", [["sync"], ["sync", "--full"]])
def test_sync_without_credentials_says_so(
    settings: Settings, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    """No key configured is a setup problem, named as one."""
    exit_code = main(argv)

    assert exit_code == 1
    assert "INTERVALS_API_KEY" in capsys.readouterr().err


def test_recompute_on_an_empty_database_succeeds(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["init"]) == 0

    assert main(["recompute", "--all"]) == 0
    output = capsys.readouterr().out
    assert "no data to recompute" in output
    # Even with nothing stored, every metric reports its progress.
    assert "Bereitschaft: noch nicht verfügbar (0/14" in output
    assert "Formkurve: noch nicht verfügbar (0/42" in output


def test_import_dir_rejects_a_path_that_is_not_a_directory(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "gibt-es-nicht"

    exit_code = main(["import-dir", str(missing)])

    assert exit_code == 1
    assert "Kein Verzeichnis" in capsys.readouterr().err


def test_import_dir_on_an_empty_directory_succeeds(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["init"]) == 0
    export = tmp_path / "garmin-export"
    export.mkdir()

    assert main(["import-dir", str(export)]) == 0
    assert "activities seen 0" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["init"],
        ["sync"],
        ["sync", "--full"],
        ["sync", "--force"],
        ["recompute", "--all"],
        ["import-dir", "/tmp"],
        ["hash-password"],
    ],
)
def test_every_documented_command_parses(argv: list[str]) -> None:
    """The four commands from the plan plus the password helper."""
    args = build_parser().parse_args(argv)

    assert callable(args.func)


def test_no_argument_is_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main([])


# --- hash-password --write ---------------------------------------------


def test_hash_password_prints_the_escaped_line(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: "ein Passwort")

    assert main(["hash-password"]) == 0

    printed = capsys.readouterr().out
    line = next(
        row for row in printed.splitlines() if row.startswith("TEMPO_PASSWORD_HASH=")
    )
    value = line.split("=", 1)[1]
    # Every separator doubled, none left single: what Compose needs.
    assert "$$" in value
    assert "$" not in value.replace("$$", "")
    assert verify_password("ein Passwort", value)


def test_hash_password_write_replaces_only_its_own_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rest of a hand-written env file survives untouched."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# Tempo\nINTERVALS_API_KEY=abc\nTEMPO_PASSWORD_HASH=alt\n\nGARMIN_EMAIL=x\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: "ein Passwort")

    assert main(["hash-password", "--write", "--env-file", str(env_file)]) == 0

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# Tempo"
    assert lines[1] == "INTERVALS_API_KEY=abc"
    assert lines[3] == ""
    assert lines[4] == "GARMIN_EMAIL=x"

    written = lines[2].split("=", 1)[1]
    assert written.startswith("scrypt$$")
    # One line, whatever the length of the hash: a wrapped hash is the other
    # way this went wrong by hand.
    assert len(lines) == 5
    assert verify_password("ein Passwort", written)


def test_hash_password_write_appends_when_the_variable_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("INTERVALS_API_KEY=abc\n", encoding="utf-8")
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: "ein Passwort")

    assert main(["hash-password", "--write", "--env-file", str(env_file)]) == 0

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "INTERVALS_API_KEY=abc"
    assert lines[1].startswith("TEMPO_PASSWORD_HASH=scrypt$$")


def test_hash_password_write_creates_the_file_when_there_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: "ein Passwort")

    assert main(["hash-password", "--write", "--env-file", str(env_file)]) == 0

    assert env_file.read_text(encoding="utf-8").startswith("TEMPO_PASSWORD_HASH=")


def test_hash_password_refuses_a_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter(["eins", "zwei"])
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: next(answers))
    env_file = tmp_path / ".env"

    assert main(["hash-password", "--write", "--env-file", str(env_file)]) == 1

    assert not env_file.exists()
