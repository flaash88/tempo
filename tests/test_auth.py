"""Password hashing round-trips and fails closed."""

from __future__ import annotations

import pytest

from tempo.api.auth import hash_password, verify_password


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
