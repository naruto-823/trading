import base64
import binascii
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models.user import User


class BrokerAccountAlreadyBoundError(RuntimeError):
    pass


def extract_broker_account_id(access_token: str) -> str:
    """Extract Longbridge AAID from a legacy token after server-side validation."""
    try:
        parts = access_token.split(".")
        if len(parts) != 3:
            raise ValueError
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        account_id = str(claims["aaid"]).strip()
        if not account_id:
            raise ValueError
        return account_id
    except (binascii.Error, KeyError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("Access Token 中没有可识别的长桥账户 ID（AAID）") from exc


def validate_broker_credentials(app_key: str, app_secret: str, access_token: str) -> str:
    """Validate credentials against Longbridge, then return the verified token's AAID."""
    from longport.openapi import Config, TradeContext

    config = Config(
        app_key=app_key,
        app_secret=app_secret,
        access_token=access_token,
    )
    balances = TradeContext(config).account_balance()
    if not balances:
        raise RuntimeError("长桥凭证验证成功，但账户余额响应为空")
    return extract_broker_account_id(access_token)


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
        and user.broker_account_id
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
) -> str:
    if username == settings.auth_username:
        raise RuntimeError("The legacy account credentials are managed through production secrets")
    account_id = validate_broker_credentials(app_key, app_secret, access_token)

    def save(auth_db: Session) -> str:
        user = auth_db.scalar(select(User).where(User.username == username))
        if user is None:
            raise RuntimeError("User not found")
        owner = auth_db.scalar(
            select(User).where(User.broker_account_id == account_id, User.username != username)
        )
        if owner is not None:
            raise BrokerAccountAlreadyBoundError("该长桥账户已绑定到其他系统用户")
        user.broker_account_id = account_id
        user.broker_app_key_encrypted = _encrypt(app_key)
        user.broker_app_secret_encrypted = _encrypt(app_secret)
        user.broker_access_token_encrypted = _encrypt(access_token)
        try:
            auth_db.commit()
        except IntegrityError:
            auth_db.rollback()
            raise BrokerAccountAlreadyBoundError("该长桥账户已绑定到其他系统用户") from None
        return account_id

    if db is not None:
        return save(db)
    else:
        with SessionLocal() as auth_db:
            return save(auth_db)
