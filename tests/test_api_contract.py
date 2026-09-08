"""The API against design/README.md.

That file maps every screen to the endpoints it reads. This checks the
mapping mechanically, so an endpoint cannot be renamed or dropped without
the screen that depends on it failing here first.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from tempo.api.app import create_app
from tempo.config import Settings
from tests import REPO_ROOT

# The endpoint list from docs/PLAN.md, phase 4.
PLANNED = {
    ("get", "/api/today"),
    ("get", "/api/activities"),
    ("get", "/api/activities/{activity_id}"),
    ("get", "/api/activities/{activity_id}/streams"),
    ("get", "/api/trends"),
    ("get", "/api/performance"),
    ("get", "/api/plan"),
    ("get", "/api/settings"),
    ("put", "/api/settings"),
    ("get", "/api/thresholds"),
    ("post", "/api/sync"),
    ("get", "/api/sync/status"),
    # Phase 5.
    ("post", "/api/ai/daily"),
    ("post", "/api/ai/activity/{activity_id}"),
    ("post", "/api/ai/plan-week"),
    ("post", "/api/ai/chat"),
}


@pytest.fixture
def spec(settings: Settings) -> dict[str, Any]:
    schema: dict[str, Any] = create_app(settings).openapi()
    return schema


def documented_endpoints() -> set[str]:
    """Endpoint paths named in the design README's screen table."""
    text = (REPO_ROOT / "design" / "README.md").read_text(encoding="utf-8")
    found: set[str] = set()
    for match in re.finditer(r"`(?:GET|PUT|POST|GET\|PUT)\s+([^`]+)`", text):
        for part in match.group(1).split(","):
            path = part.strip()
            if path.startswith("/api"):
                found.add(path)
    return found


def normalise(path: str) -> str:
    """The design README writes {id}; FastAPI names the parameter."""
    return path.replace("{id}", "{activity_id}")


def test_the_design_readme_names_endpoints_at_all() -> None:
    """If this breaks, the parsing below is checking nothing."""
    assert len(documented_endpoints()) >= 6


def test_every_endpoint_the_plan_lists_exists(spec: dict[str, Any]) -> None:
    available = {
        (method, path) for path, methods in spec["paths"].items() for method in methods
    }

    missing = PLANNED - available
    assert missing == set(), sorted(missing)


def test_every_endpoint_a_screen_reads_exists(spec: dict[str, Any]) -> None:
    paths = set(spec["paths"])
    missing = sorted(
        path
        for raw in documented_endpoints()
        if (path := normalise(raw)).split("?")[0] not in paths
    )

    assert missing == []


def test_the_ai_answer_names_the_model_that_wrote_it(spec: dict[str, Any]) -> None:
    """The footer in the design renders a model name; it comes from here."""
    answer = spec["components"]["schemas"]["AiAnswerResponse"]["properties"]

    assert "model" in answer
    assert "budget" in answer
    assert "cached" in answer


def test_no_endpoint_returns_a_bare_number_where_a_metric_belongs(
    spec: dict[str, Any],
) -> None:
    """Every metric field is an envelope, enforced through the schema.

    A response model that answered with a plain float would show up here as
    a numeric property named like a metric, with no confidence beside it.
    """
    schemas = spec["components"]["schemas"]
    metric_names = {
        "readiness",
        "form",
        "acwr",
        "trimp",
        "hr_tss",
        "r_tss",
        "decoupling",
        "efficiency_factor",
        "critical_speed",
        "vdot",
        "vo2max",
        "monotony",
        "strain",
        "hrv",
        "resting_hr",
        "sleep",
        "gap_pace_s_per_km",
    }

    offenders: list[str] = []
    for name, schema in schemas.items():
        if not name.endswith("Response"):
            continue
        for field, definition in schema.get("properties", {}).items():
            if field not in metric_names:
                continue
            reference = definition.get("$ref", "")
            if "MetricEnvelope" not in reference:
                offenders.append(f"{name}.{field}")

    assert offenders == []


def test_the_metric_envelope_carries_every_documented_field(
    spec: dict[str, Any],
) -> None:
    """The shape CLAUDE.md prescribes, checked against the published schema."""
    envelopes = [
        name
        for name in spec["components"]["schemas"]
        if name.startswith("MetricEnvelope")
    ]

    assert envelopes
    for name in envelopes:
        properties = set(spec["components"]["schemas"][name]["properties"])
        assert properties == {
            "value",
            "confidence",
            "days_of_history",
            "have",
            "required",
            "available_from",
            "last_data_point",
            "stale",
        }, name


def test_the_settings_schema_cannot_carry_a_key(spec: dict[str, Any]) -> None:
    """Not as an output, and not as an input either."""
    response = spec["components"]["schemas"]["SettingsResponse"]["properties"]
    update = spec["components"]["schemas"]["SettingsUpdate"]

    assert not any("key" in name.lower() for name in response)
    assert not any("secret" in name.lower() for name in response)
    assert not any("key" in name.lower() for name in update["properties"])
    # Anything unexpected in a PUT is rejected rather than ignored.
    assert update.get("additionalProperties") is False


def test_the_credential_status_offers_only_validity_and_a_tail(
    spec: dict[str, Any],
) -> None:
    properties = set(spec["components"]["schemas"]["CredentialStatus"]["properties"])

    assert properties == {"valid", "last4"}
