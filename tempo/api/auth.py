"""Single-user authentication primitives.

One user, one password. The hash lives in the environment, never in the
database and never in the repository. Only the hashing and verification
primitives exist in phase 1; the session cookie and the login route follow
in phase 4.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import secrets
from typing import Final

ALGORITHM: Final = "scrypt"
SALT_BYTES: Final = 16
KEY_LENGTH: Final = 32

# scrypt cost parameters. n must be a power of two; n*r*p sets both the CPU
# and the memory cost. These need roughly 64 MiB per verification, which is
# unnoticeable for one login per session and expensive to brute force.
SCRYPT_N: Final = 2**16
SCRYPT_R: Final = 8
SCRYPT_P: Final = 1

# Bumped only by the maxmem argument; scrypt needs 128 * n * r bytes.
_MAXMEM: Final = 128 * SCRYPT_N * SCRYPT_R * 2


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_LENGTH,
        maxmem=_MAXMEM,
    )


def hash_password(password: str) -> str:
    """Return a self-describing hash string, safe to put in an env var."""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(SALT_BYTES)
    derived = _derive(password, salt)
    return "$".join(
        [
            ALGORITHM,
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(derived).decode("ascii"),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    """Check a password against a stored hash, in constant time.

    A malformed or empty hash returns ``False`` rather than raising — an
    unconfigured deployment must refuse the login, not leak a stack trace.
    """
    try:
        algorithm, raw_n, raw_r, raw_p, raw_salt, raw_hash = encoded.split("$")
        if algorithm != ALGORITHM:
            return False
        n, r, p = int(raw_n), int(raw_r), int(raw_p)
        salt = base64.b64decode(raw_salt, validate=True)
        expected = base64.b64decode(raw_hash, validate=True)
    except (ValueError, TypeError):
        return False

    candidate = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=len(expected),
        maxmem=128 * n * r * 2,
    )
    return hmac.compare_digest(candidate, expected)


# --- session tokens ----------------------------------------------------

SESSION_COOKIE: Final = "tempo_session"

# A session lasts a week; the athlete opens the app daily and should not be
# asked to log in every time, but a forgotten open tab should not stay open
# forever either.
SESSION_LIFETIME: Final = dt.timedelta(days=7)

_TOKEN_VERSION: Final = "v1"
_SIGNATURE_BYTES: Final = 32

# Domain separation, so the derived key cannot collide with any other use
# of the same secret.
_KEY_CONTEXT: Final = b"tempo-session-v1"


def _signing_key(password_hash: str) -> bytes:
    """Derive the cookie signing key from the stored password hash.

    No separate secret to configure, and rotating the password invalidates
    every session — which is what changing a password is supposed to do.
    The hash never leaves the server and is not recoverable from the key.
    """
    return hashlib.blake2b(
        password_hash.encode("utf-8"), key=_KEY_CONTEXT, digest_size=_SIGNATURE_BYTES
    ).digest()


def _sign(payload: str, key: bytes) -> str:
    digest = hmac.new(key, payload.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def issue_session(
    password_hash: str, *, issued_at: dt.datetime, lifetime: dt.timedelta | None = None
) -> str:
    """A signed session token. Opaque to the client and carries no data."""
    if not password_hash:
        raise ValueError("cannot issue a session without a configured password")
    expires = issued_at + (lifetime or SESSION_LIFETIME)
    payload = f"{_TOKEN_VERSION}.{int(expires.timestamp())}"
    return f"{payload}.{_sign(payload, _signing_key(password_hash))}"


def verify_session(token: str | None, password_hash: str, *, now: dt.datetime) -> bool:
    """Check a session token, in constant time where it matters.

    Fails closed on anything unexpected: a missing token, a tampered one, an
    expired one, or a deployment with no password configured at all.
    """
    if not token or not password_hash:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    version, raw_expiry, signature = parts
    if version != _TOKEN_VERSION:
        return False

    payload = f"{version}.{raw_expiry}"
    if not hmac.compare_digest(signature, _sign(payload, _signing_key(password_hash))):
        return False

    try:
        expires = dt.datetime.fromtimestamp(int(raw_expiry), tz=dt.UTC)
    except (ValueError, OverflowError, OSError):
        return False
    return now < expires


def last4(secret: str) -> str | None:
    """The last four characters of a key, for recognising which one it is.

    Never the key itself, and nothing that could be extended back into it.
    """
    stripped = secret.strip()
    if len(stripped) < 4:
        return None
    return stripped[-4:]
