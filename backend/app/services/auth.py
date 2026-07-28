"""Small signed-cookie authentication layer for the private dashboard."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time

from app.config import settings

COOKIE_NAME = "trading_session"


def auth_is_configured() -> bool:
    return bool(settings.auth_username and settings.auth_password and settings.auth_session_secret)


def authenticate(username: str, password: str) -> bool:
    if not auth_is_configured():
        return False
    return hmac.compare_digest(username, settings.auth_username) and hmac.compare_digest(
        password, settings.auth_password
    )


def create_session(username: str) -> str:
    expires_at = int(time.time()) + settings.auth_session_hours * 3600
    payload = f"{username}:{expires_at}".encode()
    signature = hmac.new(settings.auth_session_secret.encode(), payload, hashlib.sha256).hexdigest().encode()
    return base64.urlsafe_b64encode(payload + b"." + signature).decode()


def verify_session(token: str | None) -> bool:
    if not token or not auth_is_configured():
        return False
    try:
        decoded = base64.urlsafe_b64decode(token.encode())
        payload, signature = decoded.rsplit(b".", 1)
        username, expires_raw = payload.decode().rsplit(":", 1)
        expected = hmac.new(settings.auth_session_secret.encode(), payload, hashlib.sha256).hexdigest().encode()
        return (
            hmac.compare_digest(signature, expected)
            and hmac.compare_digest(username, settings.auth_username)
            and int(expires_raw) >= int(time.time())
        )
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
