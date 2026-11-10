from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from app.services import risk_monitor as rm

UTC = timezone.utc
NOW = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)


def quote(symbol="QQQ.US", price=97, stamp=NOW, **kw):
    return SimpleNamespace(
        symbol=symbol, last_done=price, prev_close=100, timestamp=stamp, trade_status="TradeStatus.Normal", **kw
    )


def snapshot(nav=100000, flows=0, prices=None, positions=None):
    return dict(
        now=NOW.timestamp(),
        net_assets=nav,
        margin_call=0,
        prices=prices or [],
        positions=positions or {},
        external_flow=flows,
        issues=[],
        usd_hkd=7.85,
    )


def test_extended_session_never_uses_old_regular_price():
    q = quote(pre_market_quote=None, post_market_quote=None, overnight_quote=None)
    assert rm.select_quote(q, "pre", "2026-11-10", NOW) is None


def test_uses_fresh_postmarket_timestamp_and_regular_close_reference():
    q = quote(price=100, post_market_quote=SimpleNamespace(last_done=96, timestamp=NOW))
    p = rm.select_quote(q, "post", "2026-11-10", NOW)
    assert p["price"] == 96 and p["reference"] == 100 and p["session"] == "post"
    q.post_market_quote.timestamp = NOW - timedelta(minutes=10)
    assert rm.select_quote(q, "post", "2026-11-10", NOW) is None


def test_hk_quote_and_fractional_quantity_are_preserved():
    p = rm.select_quote(quote("3308.HK", 90), "regular", "2026-11-10", NOW)
    assert p["symbol"] == "3308.HK" and p["price"] == 90
    s = snapshot(prices=[p], positions={"3308.HK": {"quantity": 100, "currency": "HKD"}})
    signals, _ = rm.evaluate(s, {})
    assert any(x["key"].startswith("price:3308.HK:") for x in signals)


def test_external_withdrawal_is_not_investment_loss():
    state = {"baseline": {"key": rm.day_key(NOW), "at": NOW.timestamp() - 60, "nav": 100000}}
    signals, _ = rm.evaluate(snapshot(nav=80000, flows=-20000), state)
    assert not any(x["key"].startswith("account-loss") for x in signals)


def test_unknown_cashflow_suppresses_exact_loss_alert():
    state = {"baseline": {"key": rm.day_key(NOW), "at": NOW.timestamp() - 60, "nav": 100000}}
    signals, _ = rm.evaluate(snapshot(nav=80000, flows=None), state)
    assert not any(x["key"].startswith("account-loss") for x in signals)
    assert any(x["key"].startswith("coverage:cashflow") for x in signals)


def test_cash_conversion_trade_flows_do_not_become_external_deposits():
    rows = [
        SimpleNamespace(transaction_flow_name="Sell Contract-Stocks", currency="USD", balance=3960),
        SimpleNamespace(transaction_flow_name="Withdrawal", currency="USD", balance=-100),
    ]
    assert rm.external_cashflow(rows) == -785
    rows.append(SimpleNamespace(transaction_flow_name="unrecognised transfer", currency="USD", balance=900))
    assert rm.external_cashflow(rows) is None


def test_failed_push_retries_and_success_persists_across_restart():
    signal = {"key": "margin", "level": 2, "title": "追保", "body": "核对账户"}
    state = {}
    sent = []

    def sender(title, body, **kw):
        sent.append(title)
        return {"ok": len(sent) > 1}

    assert rm.deliver([signal], state, NOW.timestamp(), sender) == 0
    assert rm.deliver([signal], state, NOW.timestamp() + 60, sender) == 1
    import json

    restored = json.loads(json.dumps(state))
    assert rm.deliver([signal], restored, NOW.timestamp() + 120, sender) == 0
    assert len(sent) == 2


def test_severity_escalation_bypasses_cooldown_but_same_level_does_not():
    state = {}
    sent = []

    def sender(*args, **kw):
        sent.append(args)
        return {"ok": True}

    signal = {"key": "drop", "level": 1, "title": "下跌", "body": "事实"}
    assert rm.deliver([signal], state, NOW.timestamp(), sender) == 1
    assert rm.deliver([signal], state, NOW.timestamp() + 60, sender) == 0
    assert rm.deliver([{**signal, "level": 2}], state, NOW.timestamp() + 120, sender) == 1


def test_rapid_drop_needs_full_window_and_never_crosses_sessions():
    p = {
        "symbol": "QQQ.US",
        "price": 98,
        "reference": 100,
        "session": "regular",
        "session_key": "2026-11-10:regular",
        "at": NOW.timestamp(),
    }
    state = {"history": {"QQQ.US": [{**p, "price": 100, "at": NOW.timestamp() - 60}]}}
    signals, _ = rm.evaluate(snapshot(prices=[p]), state)
    assert not any(x["key"].startswith("rapid:") for x in signals)
    state["history"]["QQQ.US"][0]["at"] = NOW.timestamp() - 300
    state["history"]["QQQ.US"].append({**p, "price": 99.5, "at": NOW.timestamp() - 180})
    signals, _ = rm.evaluate(snapshot(prices=[p]), state)
    assert any(x["key"].startswith("rapid:") for x in signals)
    state["history"]["QQQ.US"][0]["session_key"] = "2026-11-10:pre"
    signals, _ = rm.evaluate(snapshot(prices=[p]), state)
    assert not any(x["key"].startswith("rapid:") for x in signals)


def test_exchange_calendar_dst_holidays_and_hk_lunch():
    days = {date(2026, 11, 9), date(2026, 11, 10)}
    assert rm.session_at("US", datetime(2026, 11, 9, 1, tzinfo=UTC), days, set())[0] == "overnight"
    assert rm.session_at("US", datetime(2026, 11, 7, 2, tzinfo=UTC), days, set())[0] == "closed"
    assert rm.session_at("US", datetime(2026, 11, 10, 14, 30, tzinfo=UTC), days, set())[0] == "regular"
    assert rm.session_at("HK", datetime(2026, 11, 10, 4, 30, tzinfo=UTC), days, set())[0] == "closed"
    assert rm.session_at("HK", datetime(2026, 11, 10, 5, tzinfo=UTC), days, set())[0] == "regular"


def test_initial_existing_short_is_reported_and_new_obligation_is_detected():
    positions = {"QQQ261113P660000.US": {"quantity": -1, "currency": "USD"}}
    signals, _ = rm.evaluate(snapshot(positions=positions), {})
    assert any(x["key"].startswith("short:") for x in signals)
    signals, _ = rm.evaluate(snapshot(positions=positions), {"positions": positions})
    assert not any(x["key"].startswith("short:") for x in signals)


def test_public_health_never_exposes_account_state(db_session):
    rm.save_state(
        db_session,
        {
            "last_poll": NOW.timestamp(),
            "last_success": NOW.timestamp(),
            "baseline": {"nav": 123456},
            "positions": {"SECRET.US": {}},
            "configured": True,
            "enabled": True,
        },
    )
    result = rm.health(db_session, NOW.timestamp())
    assert result["healthy"] is True
    assert "baseline" not in result and "positions" not in result
    assert "SECRET" not in str(result)


def test_recovered_price_can_alert_again_after_thirty_minutes():
    p = {
        "symbol": "QQQ.US",
        "price": 97,
        "reference": 100,
        "session": "regular",
        "session_key": "2026-11-10:regular",
        "at": NOW.timestamp(),
    }

    def sender(*a, **kw):
        return {"ok": True}

    signals, state = rm.evaluate(snapshot(prices=[p]), {})
    assert rm.deliver(signals, state, NOW.timestamp(), sender) == 1
    s = snapshot(prices=[{**p, "price": 100, "at": NOW.timestamp() + 2100}])
    s["now"] += 2100
    _, state = rm.evaluate(s, state)
    s["prices"] = [{**p, "at": NOW.timestamp() + 2160}]
    s["now"] += 60
    signals, state = rm.evaluate(s, state)
    assert rm.deliver(signals, state, s["now"], sender) == 1


def test_duplicate_failed_candidate_sends_only_once_per_poll():
    signal = {"key": "margin", "level": 2, "title": "追保", "body": "核对"}
    calls = []

    def sender(*a, **kw):
        calls.append(a)
        return {"ok": False}

    rm.deliver([signal, signal], {}, NOW.timestamp(), sender)
    assert len(calls) == 1


def test_reopening_same_short_contract_is_a_new_obligation():
    positions = {"QQQ261113P660000.US": {"quantity": -1, "currency": "USD"}}
    signals, state = rm.evaluate(snapshot(positions=positions), {})

    def sender(*a, **kw):
        return {"ok": True}

    assert rm.deliver(signals, state, NOW.timestamp(), sender) == 1
    _, state = rm.evaluate(snapshot(), state)
    reopened = snapshot(positions=positions)
    reopened["now"] += 120
    signals, state = rm.evaluate(reopened, state)
    assert rm.deliver(signals, state, reopened["now"], sender) == 1
