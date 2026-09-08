"""Logging is one JSON object per line on stdout."""

from __future__ import annotations

import json
import logging

import pytest

from tempo.logging import configure_logging


def test_records_are_json_lines(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    logging.getLogger("tempo.test").info("sync gestartet", extra={"source": "fit"})

    line = capsys.readouterr().out.strip()
    payload = json.loads(line)

    assert payload["level"] == "INFO"
    assert payload["logger"] == "tempo.test"
    assert payload["message"] == "sync gestartet"
    assert payload["source"] == "fit"
    assert payload["ts"].endswith("+00:00")


def test_exceptions_are_rendered_into_the_same_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging()
    try:
        raise RuntimeError("kaputt")
    except RuntimeError:
        logging.getLogger("tempo.test").exception("sync fehlgeschlagen")

    payload = json.loads(capsys.readouterr().out.strip())

    assert "RuntimeError: kaputt" in payload["exception"]


def test_configure_logging_is_idempotent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging()
    configure_logging()
    logging.getLogger("tempo.test").warning("einmal")

    lines = [line for line in capsys.readouterr().out.splitlines() if line]

    assert len(lines) == 1
