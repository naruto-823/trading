import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models.user import User


def _cipher() -> Fernet:
    if not settings.broker_credentials_key:
        raise RuntimeError("BROKER_CREDENTIALS_KEY is not configured")
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.broker_credentials_key.encode()).digest())
    return Fernet(key)


def _encrypt(value: str) -> str:
    return _cipher().encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    try:
        return _cipher().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Saved broker credentials cannot be decrypted") from exc


def _find_user(username: str, db: Session | None = None) -> User | None:
    if db is not None:
        return db.scalar(select(User).where(User.username == username))
    with SessionLocal() as auth_db:
        return auth_db.scalar(select(User).where(User.username == username))


def broker_credentials_configured(username: str, db: Session | None = None) -> bool:
    if username == settings.auth_username:
        return settings.validate_longport()
    user = _find_user(username, db)
    return bool(
        user
        and user.broker_app_key_encrypted
        and user.broker_app_secret_encrypted
        and user.broker_access_token_encrypted
    )


def get_broker_credentials(username: str) -> tuple[str, str, str]:
    if username == settings.auth_username:
        if not settings.validate_longport():
            raise RuntimeError("Longbridge credentials are not configured")
        return settings.longport_app_key, settings.longport_app_secret, settings.longport_access_token
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        if not user or not broker_credentials_configured(username, db):
            raise RuntimeError("Longbridge credentials are not configured for this user")
        return (
            _decrypt(user.broker_app_key_encrypted or ""),
            _decrypt(user.broker_app_secret_encrypted or ""),
            _decrypt(user.broker_access_token_encrypted or ""),
        )


def save_broker_credentials(
    username: str,
    app_key: str,
    app_secret: str,
    access_token: str,
    db: Session | None = None,
) -> None:
    if username == settings.auth_username:
        raise RuntimeError("The legacy account credentials are managed through production secrets")
    def save(auth_db: Session) -> None:
        user = auth_db.scalar(select(User).where(User.username == username))
        if user is None:
            raise RuntimeError("User not found")
        user.broker_app_key_encrypted = _encrypt(app_key)
        user.broker_app_secret_encrypted = _encrypt(app_secret)
        user.broker_access_token_encrypted = _encrypt(access_token)
        auth_db.commit()

    if db is not None:
        save(db)
    else:
        with SessionLocal() as auth_db:
            save(auth_db)
