"""Operator accounts and sessions.

Identity has to be real. A guard marking an alert a false alarm is writing the
label the detection layer gets tuned against, and a committee member sending a
notice is messaging every resident in the society — neither can rest on a name
someone typed into a text box.

Passwords use scrypt from the standard library rather than argon2: it is
memory-hard, it is already there on every Python that ships with OpenSSL, and
adding a compiled dependency is exactly what broke the container last time.
Sessions live server-side; the cookie carries a random token and the database
only ever sees its hash, so a stolen copy of the DB does not hand anyone a
live session.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

# scrypt parameters. n=2**15 costs ~32MB and ~60ms per hash on a small VPS,
# which is unnoticeable on a login and expensive in bulk.
_N, _R, _P, _DKLEN, _SALT = 2 ** 15, 8, 1, 32, 16

ROLES = ("guard", "committee", "admin")

# What each role may do. Guards work the gate and triage alerts; committee
# members also speak to residents; admins also change the vehicle registry and
# the accounts themselves.
PERMISSIONS: dict[str, set[str]] = {
    "guard":     {"triage", "gate"},
    "committee": {"triage", "gate", "notices"},
    "admin":     {"triage", "gate", "notices", "registry", "users"},
}

SESSION_COOKIE = "vg_session"
SESSION_TTL_S = 12 * 3600          # one shift


def hash_password(password: str) -> str:
    """-> 'scrypt$n$r$p$salt_hex$hash_hex'."""
    if not password:
        raise ValueError("password must not be empty")
    salt = os.urandom(_SALT)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P,
                        dklen=_DKLEN, maxmem=_N * _R * 256)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n), int(r), int(p)
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                            n=n, r=r, p=p, dklen=len(hash_hex) // 2,
                            maxmem=n * r * 256)
    except (ValueError, TypeError):
        return False
    # constant time: a timing difference here leaks how much of the hash matched
    return hmac.compare_digest(dk.hex(), hash_hex)


def new_token() -> tuple[str, str]:
    """Return (token_for_the_cookie, hash_to_store)."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def can(role: str, permission: str) -> bool:
    return permission in PERMISSIONS.get(role, set())


def session_expiry(now: float | None = None) -> float:
    return (now if now is not None else time.time()) + SESSION_TTL_S
