import time

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services.auth import authenticate, create_session, verify_session


def _configure(monkeypatch):
    monkeypatch.setattr(settings, "auth_username", "trader")
    monkeypatch.setattr(settings, "auth_password", "correct horse")
    monkeypatch.setattr(settings, "auth_session_secret", "s" * 64)
    monkeypatch.setattr(settings, "auth_session_hours", 1)


def test_authenticate_and_signed_session(monkeypatch):
    _configure(monkeypatch)
    assert authenticate("trader", "correct horse")
    assert not authenticate("trader", "wrong")
    assert verify_session(create_session("trader"))


def test_session_rejects_tampering(monkeypatch):
    _configure(monkeypatch)
    token = create_session("trader")
    assert not verify_session(token[:-2] + "aa")


def test_session_rejects_expired_token(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(time, "time", lambda: 1_000)
    token = create_session("trader")
    monkeypatch.setattr(time, "time", lambda: 10_000)
    assert not verify_session(token)


def test_api_login_session_and_logout(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "require_auth", True)
    monkeypatch.setattr(settings, "auth_cookie_secure", False)

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/auth/session").status_code == 401
        assert client.post("/api/auth/login", json={"username": "trader", "password": "wrong"}).status_code == 401

        login = client.post(
            "/api/auth/login",
            json={"username": "trader", "password": "correct horse"},
        )
        assert login.status_code == 200
        assert "HttpOnly" in login.headers["set-cookie"]
        assert client.get("/api/auth/session").status_code == 200

        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/auth/session").status_code == 401
