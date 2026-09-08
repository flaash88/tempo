"""Thresholds live in exactly one place and match the specification."""

from __future__ import annotations

import json

from tempo.metrics import thresholds
from tests import REPO_ROOT


def test_minimum_history_matches_the_specification() -> None:
    assert thresholds.MIN_NIGHTS_READINESS == 14
    assert thresholds.MIN_DAYS_HRV_BASELINE == 21
    assert thresholds.MIN_DAYS_FORM == 42
    assert thresholds.MIN_PERFORMANCES_CRITICAL_SPEED == 3


def test_staleness_and_baseline_reset_match_the_specification() -> None:
    assert thresholds.STALE_AFTER_DAYS == 7
    assert thresholds.BASELINE_RESET_GAP_DAYS == 14


def test_load_windows_match_the_specification() -> None:
    assert thresholds.CTL_TIME_CONSTANT_DAYS == 42
    assert thresholds.ATL_TIME_CONSTANT_DAYS == 7
    assert thresholds.ACWR_ACUTE_DAYS == 7
    assert thresholds.ACWR_CHRONIC_DAYS == 28


def test_minimum_history_entries_are_self_consistent() -> None:
    for key, entry in thresholds.MINIMUM_HISTORY.items():
        assert entry.key == key
        assert entry.required > 0
        assert entry.label_de


def test_as_dict_is_json_serialisable() -> None:
    payload = thresholds.as_dict()

    assert json.loads(json.dumps(payload)) == payload
    assert payload["minimum_history"]["form"]["required"] == 42


def test_thresholds_are_not_duplicated_in_other_modules() -> None:
    """The frontend and the rest of the backend hold no numbers of their own.

    A literal minimum history anywhere but here is exactly the drift this
    rule exists to prevent.
    """
    offenders: list[str] = []
    for path in (REPO_ROOT / "tempo").rglob("*.py"):
        if path.parts[-1] == "thresholds.py":
            continue
        text = path.read_text(encoding="utf-8")
        for marker in ("MIN_NIGHTS_READINESS =", "MIN_DAYS_FORM ="):
            if marker in text:
                offenders.append(str(path))

    assert offenders == []
