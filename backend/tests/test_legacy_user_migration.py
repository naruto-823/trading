from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal, _bootstrap_legacy_user
from app.models.user import User
from app.services.auth import hash_password


def test_legacy_user_is_migrated_to_existing_target(monkeypatch):
    suffix = "migration_test"
    source_username = f"trader_{suffix}"
    target_username = f"naruto_{suffix}"
    source_salt, source_hash = hash_password("old-password")
    target_salt, target_hash = hash_password("preserved-password")

    with SessionLocal() as db:
        db.query(User).filter(User.username.in_([source_username, target_username])).delete()
        db.add_all(
            [
                User(
                    username=source_username,
                    password_salt=source_salt,
                    password_hash=source_hash,
                    broker_account_id="AAID-MIGRATION-TEST",
                ),
                User(
                    username=target_username,
                    password_salt=target_salt,
                    password_hash=target_hash,
                ),
            ]
        )
        db.commit()

    monkeypatch.setattr(settings, "require_auth", True)
    monkeypatch.setattr(settings, "auth_username", target_username)
    monkeypatch.setattr(settings, "legacy_auth_username", source_username)
    monkeypatch.setattr(settings, "auth_password", "unused-bootstrap-password")
    monkeypatch.setattr(settings, "longport_app_key", "")
    monkeypatch.setattr(settings, "longport_app_secret", "")
    monkeypatch.setattr(settings, "longport_access_token", "")

    _bootstrap_legacy_user()
    _bootstrap_legacy_user()

    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.username == source_username)) is None
        target = db.scalar(select(User).where(User.username == target_username))
        assert target is not None
        assert target.password_salt == target_salt
        assert target.password_hash == target_hash
        assert target.broker_account_id == "AAID-MIGRATION-TEST"
        db.delete(target)
        db.commit()
