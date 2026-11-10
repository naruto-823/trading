from datetime import datetime, timezone
from types import SimpleNamespace

from app.services import risk_monitor_collector as collector


def test_quote_failure_does_not_hide_successful_margin_call():
    class Trade:
        def account_balance(self):
            return [SimpleNamespace(currency="HKD", net_assets=100000, margin_call=1)]

        def stock_positions(self):
            return SimpleNamespace(
                channels=[
                    SimpleNamespace(positions=[SimpleNamespace(symbol="QQQ.US", quantity=121.3445, currency="USD")])
                ]
            )

        def cash_flow(self, **kw):
            return []

    class Quotes:
        def trading_days(self, *args):
            raise RuntimeError("offline")

    now = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)
    data = collector.collect({}, Trade(), Quotes(), now)
    assert data["margin_call"] == 1
    assert data["positions"]["QQQ.US"]["quantity"] == 121.3445
    assert data["prices"] == [] and data["issues"]


def test_cashflow_pagination_failure_disables_nav_attribution():
    class Trade:
        def account_balance(self):
            return [SimpleNamespace(currency="HKD", net_assets=100000, margin_call=0)]

        def stock_positions(self):
            return SimpleNamespace(channels=[])

        def cash_flow(self, **kw):
            raise RuntimeError("incomplete")

    now = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)
    state = {"baseline": {"key": "2026-11-10", "at": now.timestamp() - 300, "nav": 100000}}
    data = collector.collect(state, Trade(), SimpleNamespace(), now)
    assert data["external_flow"] is None


def test_nonpositive_equity_does_not_hide_margin_and_benchmark_is_always_watched():
    class Trade:
        def account_balance(self):
            return [SimpleNamespace(currency="HKD", net_assets=-100, margin_call=1)]

        def stock_positions(self):
            return SimpleNamespace(channels=[])

        def cash_flow(self, **kw):
            return []

    class Quotes:
        def trading_days(self, *args):
            return SimpleNamespace(trading_days=[now.date()], half_trading_days=[])

        def quote(self, symbols):
            assert symbols == ["QQQ.US"]
            return [SimpleNamespace(symbol="QQQ.US", last_done=97, prev_close=100, timestamp=now)]

    now = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)
    data = collector.collect({}, Trade(), Quotes(), now)
    assert data["margin_call"] and data["net_assets"] == -100
    assert [p["symbol"] for p in data["prices"]] == ["QQQ.US"]
