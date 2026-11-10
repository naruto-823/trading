from contextlib import nullcontext
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.db import current_username, reset_current_username, set_current_username
from app.services.risk_monitor import load_state, save_state
from app.workers import risk_monitor_worker as worker


def test_worker_keeps_default_account_even_when_manually_triggered_by_other_user(monkeypatch, db_session):
    from app.longbridge import client
    from app.services import risk_monitor_collector

    monkeypatch.setattr(worker.settings, "risk_monitor_enabled", True)
    monkeypatch.setattr(worker, "SessionLocal", lambda: nullcontext(db_session))
    marker = object()

    def trade():
        assert current_username() == worker.settings.auth_username
        return marker

    def unavailable():
        raise RuntimeError("quote unavailable")

    def collect(state, trade, quotes, now, rate):
        assert trade is marker and quotes is None
        return dict(
            now=datetime.now(timezone.utc).timestamp(),
            net_assets=100000,
            margin_call=True,
            usd_hkd=7.85,
            positions={},
            prices=[],
            issues=["行情不可用"],
            external_flow=0,
        )

    pushed = []
    monkeypatch.setattr(client, "get_trade_context", trade)
    monkeypatch.setattr(client, "get_quote_context", unavailable)
    monkeypatch.setattr(risk_monitor_collector, "collect", collect)
    monkeypatch.setattr(worker, "send_bark", lambda *a, **kw: pushed.append(a) or {"ok": True})
    token = set_current_username("another-tenant")
    try:
        worker.run_once()
        assert current_username() == "another-tenant"
    finally:
        reset_current_username(token)
    assert any("追保" in args[0] for args in pushed)
    assert load_state(db_session)["last_success"]


def test_another_tenant_cannot_read_owner_monitor_status():
    from app.api.system import get_risk_monitor

    token = set_current_username("another-tenant")
    try:
        with pytest.raises(HTTPException) as exc:
            get_risk_monitor()
        assert exc.value.status_code == 403
    finally:
        reset_current_username(token)


def test_pending_urgent_push_retries_even_when_broker_is_now_unavailable(monkeypatch, db_session):
    from app.longbridge import client

    monkeypatch.setattr(worker.settings, "risk_monitor_enabled", True)
    monkeypatch.setattr(worker, "SessionLocal", lambda: nullcontext(db_session))

    def unavailable():
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(client, "get_trade_context", unavailable)
    save_state(
        db_session,
        {"pending": {"margin-old": {"key": "margin-old", "level": 2, "title": "追保", "body": "采集时间见历史"}}},
    )
    pushed = []
    monkeypatch.setattr(worker, "send_bark", lambda *a, **kw: pushed.append(a) or {"ok": True})
    worker.run_once()
    assert any(args[0] == "追保" for args in pushed)
    assert not load_state(db_session)["pending"]
