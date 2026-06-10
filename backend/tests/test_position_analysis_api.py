from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db import Base, get_db
from app.main import app
from app.models.position_analysis_report import PositionAnalysisReport


def _client_with_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), TestingSession


def test_latest_endpoint_returns_report():
    client, TestingSession = _client_with_db()
    db = TestingSession()
    db.add(PositionAnalysisReport(generated_at=datetime.utcnow(), summary="最新体检"))
    db.commit()
    db.close()
    try:
        resp = client.get("/api/position-analysis/latest")
        assert resp.status_code == 200
        assert resp.json()["data"]["summary"] == "最新体检"
    finally:
        app.dependency_overrides.clear()


def test_latest_endpoint_empty_returns_null_data():
    client, _ = _client_with_db()
    try:
        resp = client.get("/api/position-analysis/latest")
        assert resp.status_code == 200
        assert resp.json()["data"] is None
    finally:
        app.dependency_overrides.clear()
