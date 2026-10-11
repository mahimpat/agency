"""Passwords, CSRF tokens and password-reset links."""

import hashlib
import hmac
import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        check = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(check.hex(), digest)
    except (ValueError, TypeError):
        return False


def csrf_token(session: dict) -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def csrf_ok(session: dict, submitted: str | None) -> bool:
    expected = session.get("csrf")
    return bool(expected and submitted and hmac.compare_digest(expected, submitted))


def _reset_serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt="password-reset")


def make_reset_token(secret_key: str, user_id: int, password_hash: str) -> str:
    # Includes part of the current hash, so a link stops working once it has been used.
    return _reset_serializer(secret_key).dumps({"u": user_id, "h": password_hash[-12:]})


def read_reset_token(secret_key: str, token: str, max_age: int = 3600) -> dict | None:
    try:
        return _reset_serializer(secret_key).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


def make_verify_token(secret_key: str, user_id: int, email: str) -> str:
    return URLSafeTimedSerializer(secret_key, salt="verify-email").dumps({"u": user_id, "e": email})


def read_verify_token(secret_key: str, token: str, max_age: int = 7 * 24 * 3600) -> dict | None:
    try:
        return URLSafeTimedSerializer(secret_key, salt="verify-email").loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
