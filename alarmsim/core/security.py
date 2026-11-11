from __future__ import annotations

import hashlib
import hmac
import secrets

ITERATIONS = 310_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters))
    return algo == "pbkdf2_sha256" and hmac.compare_digest(dk.hex(), digest)


def new_api_key() -> str:
    return "as_" + secrets.token_urlsafe(32)
