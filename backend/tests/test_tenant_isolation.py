from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_auth_db, get_db, reset_current_username, set_current_username
from app.main import app
from app.models.position import Position


def _tenant_db(username: str):
    token = set_current_username(username)
    dependency = get_db()
    db = next(dependency)
    return token, dependency, db


def test_tenant_databases_are_physically_isolated():
    first_username = f"tenant_a_{uuid4().hex}"
    second_username = f"tenant_b_{uuid4().hex}"

    token, dependency, first_db = _tenant_db(first_username)
    try:
        first_db.add(Position(symbol="AAPL.US", market="US", quantity=7))
        first_db.commit()
        assert first_db.query(Position).count() == 1
    finally:
        dependency.close()
        reset_current_username(token)

    token, dependency, second_db = _tenant_db(second_username)
    try:
        assert second_db.query(Position).count() == 0
    finally:
        dependency.close()
        reset_current_username(token)


def test_two_authenticated_users_cannot_see_each_others_records(monkeypatch, db_session: Session):
    monkeypatch.setattr(settings, "require_auth", True)
    monkeypatch.setattr(settings, "auth_username", "legacy_admin")
    monkeypatch.setattr(settings, "auth_password", "legacy-password")
    monkeypatch.setattr(settings, "auth_session_secret", "s" * 64)
    monkeypatch.setattr(settings, "auth_cookie_secure", False)
    monkeypatch.setattr(settings, "broker_credentials_key", "k" * 64)
    monkeypatch.setattr(settings, "longport_app_key", "")
    monkeypatch.setattr(settings, "longport_app_secret", "")
    monkeypatch.setattr(settings, "longport_access_token", "")
    monkeypatch.setattr(
        "app.services.broker_credentials.validate_broker_credentials",
        lambda *_args: "AAID-FIRST-USER",
    )

    def override_auth_db():
        yield db_session

    app.dependency_overrides[get_auth_db] = override_auth_db
    first_username = f"user_a_{uuid4().hex[:12]}"
    second_username = f"user_b_{uuid4().hex[:12]}"
    try:
        with TestClient(app) as first_client, TestClient(app) as second_client:
            for client, username in ((first_client, first_username), (second_client, second_username)):
                response = client.post(
                    "/api/auth/register",
                    json={"username": username, "password": "strong-pass-123"},
                )
                assert response.status_code == 201

            created = first_client.post(
                "/api/alerts",
                json={"symbol": "AAPL.US", "condition": "price_above", "threshold": 999},
            )
            assert created.status_code == 200
            assert len(first_client.get("/api/alerts").json()["data"]) == 1
            assert second_client.get("/api/alerts").json()["data"] == []

            assert first_client.get("/api/broker/credentials").json()["data"]["configured"] is False
            saved = first_client.put(
                "/api/broker/credentials",
                json={
                    "app_key": "first-user-app-key",
                    "app_secret": "first-user-app-secret-value",
                    "access_token": "first-user-access-token-value-1234567890",
                },
            )
            assert saved.status_code == 200
            assert first_client.get("/api/broker/credentials").json()["data"]["configured"] is True
            assert second_client.get("/api/broker/credentials").json()["data"]["configured"] is False
            duplicate = second_client.put(
                "/api/broker/credentials",
                json={
                    "app_key": "second-user-app-key",
                    "app_secret": "second-user-app-secret-value",
                    "access_token": "second-user-access-token-value-1234567890",
                },
            )
            assert duplicate.status_code == 409
    finally:
        app.dependency_overrides.clear()
