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
def built(tmp_path: Path) -> Path:
    """A minimal frontend build, in the shape vite produces."""
    root = tmp_path / "web" / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>Tempo</title>", "utf-8")
    (root / "sw.js").write_text("self.addEventListener('install', () => {});", "utf-8")
    (root / "manifest.webmanifest").write_text(
        json.dumps({"name": "Tempo", "display": "standalone"}), "utf-8"
    )
    (root / "assets" / "index-abc123.js").write_text("console.log(1)", "utf-8")
    return root


@pytest.fixture
def client(settings: Settings, built: Path) -> Iterator[TestClient]:
    configured = settings.model_copy(update={"web_dir": built})
    with TestClient(
        create_app(configured), base_url="https://testserver"
    ) as test_client:
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


@pytest.mark.parametrize(
    "path",
    [
        "/api/gibt-es-nicht",
        "/health/tiefer",
        "/docs/gibt-es-nicht",
        "/openapi.json.bak",
    ],
)
def test_an_unknown_server_path_is_not_answered_with_the_shell(
    client: TestClient, path: str
) -> None:
    """An SPA fallback that swallows these turns a 404 into a broken parse."""
    response = client.get(path)

    assert response.status_code == 404
    assert "<title>" not in response.text


def test_the_server_paths_themselves_still_answer(client: TestClient) -> None:
    """The mount claims "/", so everything the server owns has to come first."""
    assert client.get("/health").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_a_deployment_without_a_build_still_serves_the_api(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No frontend is not a broken deployment, just an API-only one."""
    monkeypatch.chdir(tmp_path)
    nowhere = settings.model_copy(update={"web_dir": tmp_path / "nirgends"})

    assert find_frontend(nowhere.web_dir) is None
    with TestClient(create_app(nowhere), base_url="https://testserver") as client:
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 404


def test_mounting_reports_whether_it_found_anything(
    settings: Settings, built: Path
) -> None:
    app = create_app(settings.model_copy(update={"web_dir": built}))

    assert mount_frontend(app, built) is True
    assert app.state.frontend is True


def test_the_build_is_found_beside_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The container's layout: /app/web/dist, with /app as the workdir.

    Deliberately not derived from the package's own location — whether the
    package is installed as a copy or as a link decides where __file__
    points, and the app's reachability must not hang on that.
    """
    root = tmp_path / "web" / "dist"
    root.mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html>", "utf-8")
    monkeypatch.chdir(tmp_path)

    assert find_frontend() == root


def test_the_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    elsewhere = tmp_path / "woanders"
    elsewhere.mkdir()
    (elsewhere / "index.html").write_text("<!doctype html>", "utf-8")
    beside = tmp_path / "web" / "dist"
    beside.mkdir(parents=True)
    (beside / "index.html").write_text("<!doctype html>", "utf-8")
    monkeypatch.chdir(tmp_path)

    assert find_frontend(elsewhere) == elsewhere
