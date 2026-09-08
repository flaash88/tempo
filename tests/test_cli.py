"""The CLI skeleton: init works, the later phases say so plainly."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect

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
    assert "Phase 3" in output


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
