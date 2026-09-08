"""The health endpoint answers in every deployment state."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tempo import __version__
from tempo.api.app import create_app
from tempo.config import Settings
from tempo.db.migrate import upgrade_to_head


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_health_reports_a_missing_schema_before_init(client: TestClient) -> None:
    """Right after install the database exists but has no tables yet."""
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "missing"
    assert body["schema_revision"] is None


def test_health_reports_the_applied_revision_after_init(
    settings: Settings,
) -> None:
    upgrade_to_head()

    with TestClient(create_app(settings)) as client:
        body = client.get("/health").json()

    assert body["database"] == "ok"
    assert body["schema_revision"] == "0001"
    assert body["version"] == __version__


def test_health_leaks_no_configuration(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INTERVALS_API_KEY", "super-secret-key")

    with TestClient(create_app(settings)) as client:
        raw = client.get("/health").text

    assert "super-secret-key" not in raw
    assert set(client.get("/health").json()) == {
        "status",
        "version",
        "database",
        "schema_revision",
    }
