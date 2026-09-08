"""One user, one password, one cookie — and everything else closed.

Cloudflare Access is not assumed anywhere: an unauthenticated request is
refused here whatever a proxy in front might claim.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tempo.api.app import create_app
from tempo.api.auth import (
    SESSION_COOKIE,
    hash_password,
    issue_session,
    last4,
    verify_session,
)
from tempo.config import Settings, load_settings
from tempo.db.migrate import upgrade_to_head

PASSWORD = "ein sehr langes Passwort"
# Hashed once for the module: scrypt is deliberately expensive, which is the
# point in production and pure waste once per test.
PASSWORD_HASH = hash_password(PASSWORD)
NOW = dt.datetime(2026, 9, 8, 6, 0, tzinfo=dt.UTC)

# Every path that must refuse an anonymous caller.
GUARDED = [
    ("GET", "/api/today"),
    ("GET", "/api/activities"),
    ("GET", "/api/activities/i1"),
    ("GET", "/api/activities/i1/streams"),
    ("GET", "/api/trends"),
    ("GET", "/api/performance"),
    ("GET", "/api/plan"),
    ("GET", "/api/settings"),
    ("PUT", "/api/settings"),
    ("GET", "/api/thresholds"),
    ("POST", "/api/sync"),
    ("GET", "/api/sync/status"),
]


@pytest.fixture
def configured(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Settings]:
    monkeypatch.setenv("TEMPO_PASSWORD_HASH", PASSWORD_HASH)
    upgrade_to_head()
    yield load_settings()


@pytest.fixture
def client(configured: Settings) -> Iterator[TestClient]:
    # https, because the session cookie is Secure and a client would not send
    # it back over plain http — which is the point of the flag.
    with TestClient(
        create_app(configured), base_url="https://testserver"
    ) as test_client:
        yield test_client


def login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"password": PASSWORD})
    assert response.status_code == 200


# --- session tokens ----------------------------------------------------


def test_a_token_round_trips() -> None:
    token = issue_session(PASSWORD_HASH, issued_at=NOW)

    assert verify_session(token, PASSWORD_HASH, now=NOW) is True


def test_a_token_expires() -> None:
    stored = hash_password(PASSWORD)
    token = issue_session(stored, issued_at=NOW, lifetime=dt.timedelta(minutes=5))

    assert verify_session(token, stored, now=NOW + dt.timedelta(minutes=4)) is True
    assert verify_session(token, stored, now=NOW + dt.timedelta(minutes=6)) is False


def test_changing_the_password_ends_every_session() -> None:
    """Which is what changing a password is for."""
    token = issue_session(PASSWORD_HASH, issued_at=NOW)

    assert verify_session(token, hash_password("etwas anderes"), now=NOW) is False


@pytest.mark.parametrize(
    "token",
    ["", "nonsense", "v1.999", "v1.9999999999.badsignature", "v2.1.2"],
)
def test_a_tampered_token_is_refused(token: str) -> None:
    assert verify_session(token, PASSWORD_HASH, now=NOW) is False


def test_a_token_cannot_have_its_expiry_edited() -> None:
    token = issue_session(
        PASSWORD_HASH, issued_at=NOW, lifetime=dt.timedelta(minutes=5)
    )
    version, expiry, signature = token.split(".")
    extended = f"{version}.{int(expiry) + 86_400}.{signature}"

    assert verify_session(extended, PASSWORD_HASH, now=NOW) is False


def test_no_session_without_a_configured_password() -> None:
    assert verify_session("anything", "", now=NOW) is False
    with pytest.raises(ValueError, match="configured password"):
        issue_session("", issued_at=NOW)


def test_the_token_carries_nothing_but_an_expiry_and_a_signature() -> None:
    token = issue_session(PASSWORD_HASH, issued_at=NOW)

    assert PASSWORD not in token
    assert PASSWORD_HASH not in token
    assert len(token.split(".")) == 3


# --- last4 -------------------------------------------------------------


def test_last4_is_the_tail_and_nothing_else() -> None:
    assert last4("sk-ant-api03-abcd7f2c") == "7f2c"


def test_a_short_secret_has_no_recognisable_tail() -> None:
    assert last4("abc") is None
    assert last4("") is None


# --- the login flow ----------------------------------------------------


def test_the_right_password_sets_an_http_only_cookie(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"password": PASSWORD})

    assert response.status_code == 200
    assert response.json() == {"authenticated": True, "configured": True}
    header = response.headers["set-cookie"]
    assert header.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in header
    assert "Secure" in header
    assert "SameSite=lax" in header.replace("SameSite=Lax", "SameSite=lax")
    assert "Path=/" in header


def test_the_wrong_password_is_refused(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"password": "falsch"})

    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def test_the_password_never_comes_back_in_the_answer(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"password": PASSWORD})

    assert PASSWORD not in response.text


def test_logout_clears_the_cookie(client: TestClient) -> None:
    login(client)

    response = client.post("/api/auth/logout")

    assert response.status_code == 200
    assert response.json()["authenticated"] is False
    assert client.get("/api/today").status_code == 401


def test_the_session_endpoint_answers_without_one(client: TestClient) -> None:
    """So the interface can decide whether to show a login screen."""
    response = client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json() == {"authenticated": False, "configured": True}


def test_the_session_endpoint_reports_a_live_session(client: TestClient) -> None:
    login(client)

    assert client.get("/api/auth/session").json()["authenticated"] is True


def test_the_cookie_is_not_sent_back_over_plain_http(
    configured: Settings,
) -> None:
    """Secure is not decoration: the browser withholds it without TLS."""
    with TestClient(create_app(configured), base_url="http://testserver") as insecure:
        insecure.post("/api/auth/login", json={"password": PASSWORD})

        assert insecure.get("/api/today").status_code == 401


# --- the gate ----------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), GUARDED)
def test_every_api_path_refuses_an_anonymous_caller(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, path, json={} if method == "PUT" else None)

    assert response.status_code == 401, path


def test_a_forged_cookie_does_not_get_in(client: TestClient) -> None:
    client.cookies.set(SESSION_COOKIE, "v1.99999999999.forged")

    assert client.get("/api/today").status_code == 401


def test_health_stays_open(client: TestClient) -> None:
    """The tunnel and the container health check need it."""
    assert client.get("/health").status_code == 200


def test_an_unconfigured_deployment_refuses_everything(
    settings: Settings,
) -> None:
    """An unfinished setup is not an open door."""
    upgrade_to_head()
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        assert client.get("/api/today").status_code == 401
        assert (
            client.post("/api/auth/login", json={"password": "irgendwas"}).status_code
            == 503
        )
        assert client.get("/api/auth/session").json() == {
            "authenticated": False,
            "configured": False,
        }
