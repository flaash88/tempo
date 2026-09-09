"""Password hashing round-trips and fails closed."""

from __future__ import annotations

import datetime as dt

import pytest

from tempo.api.auth import (
    hash_password,
    hash_problem,
    issue_session,
    verify_password,
    verify_session,
)


def test_hash_and_verify_round_trip() -> None:
    encoded = hash_password("ein sehr langes Passwort")

    assert verify_password("ein sehr langes Passwort", encoded) is True


def test_a_wrong_password_is_rejected() -> None:
    encoded = hash_password("richtig")

    assert verify_password("falsch", encoded) is False


def test_the_hash_does_not_contain_the_password() -> None:
    encoded = hash_password("Klartext123")

    assert "Klartext123" not in encoded
    assert encoded.startswith("scrypt$")


def test_two_hashes_of_the_same_password_differ() -> None:
    assert hash_password("gleich") != hash_password("gleich")


@pytest.mark.parametrize(
    "encoded",
    ["", "not-a-hash", "scrypt$$$$", "bcrypt$1$2$3$c2FsdA==$aGFzaA==", "$$$$$"],
)
def test_a_malformed_hash_refuses_the_login(encoded: str) -> None:
    """An unconfigured deployment must refuse, not raise."""
    assert verify_password("irgendwas", encoded) is False


def test_an_empty_password_cannot_be_hashed() -> None:
    with pytest.raises(ValueError, match="empty"):
        hash_password("")


# --- what a mangled hash has to say for itself -------------------------


def test_a_well_formed_hash_has_no_problem() -> None:
    assert hash_problem(hash_password("ein Passwort")) is None


def test_an_escaped_hash_is_understood_and_verifies() -> None:
    """Compose's escape must not turn into a wrong password.

    ``uv run --env-file .env`` hands the doubled form through unchanged, so
    the escaped line has to work under both readers or the same file would
    behave differently depending on how the app was started.
    """
    encoded = hash_password("ein Passwort")
    escaped = encoded.replace("$", "$$")

    assert escaped != encoded
    assert hash_problem(escaped) is None
    assert verify_password("ein Passwort", escaped)
    assert not verify_password("etwas anderes", escaped)


def test_the_escaped_and_plain_forms_sign_the_same_session() -> None:
    """Otherwise a restart under the other reader would log the athlete out."""
    encoded = hash_password("ein Passwort")
    now = dt.datetime(2026, 9, 9, 6, 0, tzinfo=dt.UTC)

    token = issue_session(encoded, issued_at=now)

    assert verify_session(token, encoded.replace("$", "$$"), now=now)


def test_a_truncated_hash_names_the_dollar_signs() -> None:
    """The exact failure from the deployment: Compose ate the separators."""
    encoded = hash_password("ein Passwort")
    # What is left after $65536, $8, $1 and the rest are substituted away.
    truncated = encoded.split("$", 1)[0]

    problem = hash_problem(truncated)

    assert problem is not None
    assert "$-Zeichen" in problem
    assert "verdoppeln" in problem


def test_an_empty_hash_says_so_rather_than_blaming_the_password() -> None:
    problem = hash_problem("   ")

    assert problem is not None
    assert "nicht gesetzt" in problem


def test_a_corrupted_hash_is_named_as_corrupted() -> None:
    assert hash_problem("scrypt$65536$8$1$nicht-base64$auch-nicht") is not None
    assert hash_problem("argon2$1$1$1$AAAA$AAAA") is not None


def test_quotes_and_whitespace_do_not_break_a_hash() -> None:
    encoded = hash_password("ein Passwort")

    assert verify_password("ein Passwort", f'  "{encoded}"  ')
