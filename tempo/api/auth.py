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


# How many "$"-separated fields a well formed hash has: the algorithm, the
# three cost parameters, the salt and the derived key.
_HASH_FIELDS: Final = 6


def normalise_hash(encoded: str) -> str:
    """Undo the escaping a deployment may have needed to survive its file.

    The hash is six fields joined by ``$``, and ``$`` is the one character
    a value in an env file cannot carry unescaped: Docker Compose reads
    ``$abc`` as a variable and substitutes it away, which silently truncates
    the hash. Doubling the sign is the documented escape, and
    ``tempo hash-password --write`` writes it that way.

    Compose turns ``$$`` back into ``$`` before the process sees it, but
    ``uv run --env-file .env`` does not — so the doubled form has to be
    understood here as well, or the same file would work under one and fail
    under the other. Neither base64 nor a decimal number ever contains a
    ``$``, so collapsing them cannot corrupt a valid hash.

    Surrounding quotes and whitespace go too: they are the other two ways a
    hash arrives not quite as it was written.
    """
    cleaned = encoded.strip().strip("\"'")
    return cleaned.replace("$$", "$")


def hash_problem(encoded: str) -> str | None:
    """Why the configured hash cannot work, in German, or ``None``.

    Checked at start-up and named at the login, because "Passwort falsch"
    is a lie when the hash never arrived intact — and it sends the athlete
    looking for the mistake in the one place it is not.
    """
    if not encoded.strip():
        return "TEMPO_PASSWORD_HASH ist nicht gesetzt"

    normalised = normalise_hash(encoded)
    parts = normalised.split("$")
    if len(parts) != _HASH_FIELDS:
        return "TEMPO_PASSWORD_HASH unvollständig — $-Zeichen in .env verdoppeln"
    algorithm, raw_n, raw_r, raw_p, raw_salt, raw_hash = parts
    if algorithm != ALGORITHM:
        return f"TEMPO_PASSWORD_HASH nennt ein unbekanntes Verfahren: {algorithm}"
    for raw in (raw_n, raw_r, raw_p):
        if not raw.isdigit():
            return "TEMPO_PASSWORD_HASH unvollständig — $-Zeichen in .env verdoppeln"
    try:
        salt = base64.b64decode(raw_salt, validate=True)
        derived = base64.b64decode(raw_hash, validate=True)
    except (ValueError, TypeError):
        return "TEMPO_PASSWORD_HASH ist beschädigt — bitte neu erzeugen"
    if len(salt) != SALT_BYTES or len(derived) != KEY_LENGTH:
        return "TEMPO_PASSWORD_HASH ist beschädigt — bitte neu erzeugen"
    return None


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
        algorithm, raw_n, raw_r, raw_p, raw_salt, raw_hash = normalise_hash(
            encoded
        ).split("$")
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

    Normalised first, so the same deployment does not get two different
    keys depending on whether Compose or ``--env-file`` read the escaped
    form — that would log the athlete out at every restart.
    """
    return hashlib.blake2b(
        normalise_hash(password_hash).encode("utf-8"),
        key=_KEY_CONTEXT,
        digest_size=_SIGNATURE_BYTES,
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
