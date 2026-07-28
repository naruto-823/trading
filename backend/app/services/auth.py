"""Small signed-cookie authentication layer for the private dashboard."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User

COOKIE_NAME = "trading_session"


def auth_is_configured() -> bool:
    return bool(settings.auth_username and settings.auth_password and settings.auth_session_secret)


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    password_hash = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex(), password_hash.hex()


def authenticate(username: str, password: str, db: Session | None = None) -> bool:
    if not auth_is_configured():
        return False
    if hmac.compare_digest(username, settings.auth_username) and hmac.compare_digest(
        password, settings.auth_password
    ):
        return True
    if db is None:
        return False
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        return False
    _, candidate = hash_password(password, bytes.fromhex(user.password_salt))
    return hmac.compare_digest(candidate, user.password_hash)


def create_session(username: str) -> str:
    expires_at = int(time.time()) + settings.auth_session_hours * 3600
    payload = f"{username}:{expires_at}".encode()
    signature = hmac.new(settings.auth_session_secret.encode(), payload, hashlib.sha256).hexdigest().encode()
    return base64.urlsafe_b64encode(payload + b"." + signature).decode()


def session_username(token: str | None) -> str | None:
    if not token or not auth_is_configured():
        return None
    try:
        decoded = base64.urlsafe_b64decode(token.encode())
        payload, signature = decoded.rsplit(b".", 1)
        username, expires_raw = payload.decode().rsplit(":", 1)
        expected = hmac.new(settings.auth_session_secret.encode(), payload, hashlib.sha256).hexdigest().encode()
        valid = (
            hmac.compare_digest(signature, expected)
            and int(expires_raw) >= int(time.time())
        )
        return username if valid else None
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None


def verify_session(token: str | None) -> bool:
    return session_username(token) is not None
