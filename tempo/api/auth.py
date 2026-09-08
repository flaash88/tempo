"""Single-user authentication primitives.

One user, one password. The hash lives in the environment, never in the
database and never in the repository. Only the hashing and verification
primitives exist in phase 1; the session cookie and the login route follow
in phase 4.
"""

from __future__ import annotations

import base64
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
