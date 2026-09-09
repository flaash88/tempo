"""Serving the built frontend, and the two files that may not be cached.

The rule from docs/PLAN.md — ``sw.js`` and ``manifest.webmanifest`` are
never cached — is enforced in two places, and this is the second one. The
first is the build, which keeps both out of the precache; a test for that
lives in the frontend suite. Here it is the HTTP header, which is what a
browser's ordinary cache goes by.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tempo.api.app import create_app
from tempo.api.static import find_frontend, mount_frontend
from tempo.config import Settings


@pytest.fixture
def built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A minimal frontend build, in the shape vite produces."""
    root = tmp_path / "web" / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>Tempo</title>", "utf-8")
    (root / "sw.js").write_text("self.addEventListener('install', () => {});", "utf-8")
    (root / "manifest.webmanifest").write_text(
        json.dumps({"name": "Tempo", "display": "standalone"}), "utf-8"
    )
    (root / "assets" / "index-abc123.js").write_text("console.log(1)", "utf-8")
    monkeypatch.setattr("tempo.api.static.CANDIDATES", (root,))
    return root


@pytest.fixture
def client(settings: Settings, built: Path) -> Iterator[TestClient]:
    with TestClient(create_app(settings), base_url="https://testserver") as test_client:
        yield test_client


def test_the_service_worker_is_never_cached(client: TestClient) -> None:
    response = client.get("/sw.js")

    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    # Without this header the worker may only control /assets, which is not
    # where the app's routes are.
    assert response.headers["service-worker-allowed"] == "/"


def test_the_manifest_is_never_cached(client: TestClient) -> None:
    response = client.get("/manifest.webmanifest")

    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["content-type"].startswith("application/manifest+json")


def test_hashed_assets_are_cached_forever(client: TestClient) -> None:
    """Their name changes when they do, so nothing else has to expire."""
    response = client.get("/assets/index-abc123.js")

    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]


def test_the_shell_is_revalidated_every_time(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_a_client_side_route_serves_the_shell(client: TestClient) -> None:
    """/trends is a route in the app, not a file on disk."""
    response = client.get("/trends")

    assert response.status_code == 200
    assert "<title>Tempo</title>" in response.text


def test_the_api_still_wins_over_the_frontend(client: TestClient) -> None:
    """Mounting "/" must not swallow the endpoints."""
    assert client.get("/health").status_code == 200
    # Unauthenticated, but reached: a 404 here would mean the mount ate it.
    assert client.get("/api/today").status_code == 401


def test_an_unknown_api_path_is_not_answered_with_the_shell(
    client: TestClient,
) -> None:
    """An SPA fallback that swallows /api/… turns a 404 into a broken parse."""
    response = client.get("/api/gibt-es-nicht")

    assert response.status_code == 404
    assert "<title>" not in response.text


def test_a_deployment_without_a_build_still_serves_the_api(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No frontend is not a broken deployment, just an API-only one."""
    monkeypatch.setattr("tempo.api.static.CANDIDATES", (tmp_path / "nirgends",))

    assert find_frontend() is None
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 404


def test_mounting_reports_whether_it_found_anything(
    settings: Settings, built: Path
) -> None:
    app = create_app(settings)

    assert mount_frontend(app) is True
    assert app.state.frontend is True
