"""Collect direct broker facts without modifying dashboard data or any orders."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite

from app.services.risk_monitor import OPTION, day_key, external_cashflow, select_quote, session_at

_calendars: dict = {}


def collect(state: dict, trade, quotes, now: datetime, usd_hkd: float = 7.85) -> dict:
    balances = trade.account_balance()
    balance = next((b for b in balances if b.currency == "HKD"), None)
    if balance is None or not isfinite(float(balance.net_assets)):
        raise ValueError("missing_valid_hkd_account")
    result = {
        "now": now.timestamp(),
        "net_assets": float(balance.net_assets),
        "margin_call": float(balance.margin_call) > 0,
        "usd_hkd": usd_hkd,
        "positions": state.get("positions", {}),
        "prices": [],
        "issues": [],
        "external_flow": None,
    }
    try:
        result["positions"] = {
            str(p.symbol): {"quantity": float(p.quantity), "currency": str(p.currency)}
            for channel in trade.stock_positions().channels
            for p in channel.positions
            if float(p.quantity) != 0
        }
    except Exception:
        result["issues"].append("持仓查询失败")
    baseline = state.get("baseline", {})
    start = baseline.get("at", now.timestamp()) if baseline.get("key") == day_key(now) else now.timestamp()
    try:
        flows = []
        for page in range(1, 21):
            chunk = trade.cash_flow(
                start_at=datetime.fromtimestamp(start, timezone.utc), end_at=now, page=page, size=100
            )
            flows.extend(chunk)
            if len(chunk) < 100:
                result["external_flow"] = external_cashflow(flows, usd_hkd)
                break
        if result["external_flow"] is None:
            result["issues"].append("资金流水类型或分页不完整")
    except Exception:
        result["issues"].append("资金流水查询失败")
    stocks = sorted({"QQQ.US"} | {s for s in result["positions"] if not OPTION.match(s) and s.endswith((".US", ".HK"))})
    for market in ("US", "HK"):
        symbols = [s for s in stocks if s.endswith("." + market)]
        if not symbols:
            continue
        try:
            from longport.openapi import Market

            cache_key = (id(quotes), market, now.date())
            calendar = _calendars.get(cache_key)
            if calendar is None:
                response = quotes.trading_days(
                    getattr(Market, market), (now - timedelta(days=2)).date(), (now + timedelta(days=3)).date()
                )
                calendar = (
                    set(response.trading_days) | set(response.half_trading_days),
                    set(response.half_trading_days),
                )
                if len(_calendars) > 8:
                    _calendars.clear()
                _calendars[cache_key] = calendar
            session, trading_day = session_at(market, now, *calendar)
            if session == "closed":
                continue
            returned = {str(q.symbol): q for q in quotes.quote(symbols)}
            for symbol in symbols:
                q = returned.get(symbol)
                p = select_quote(q, session, trading_day, now) if q is not None else None
                if p:
                    result["prices"].append(p)
                else:
                    result["issues"].append(f"{symbol} {session}行情缺失或过期")
        except Exception:
            result["issues"].append(f"{market}交易日历或行情查询失败")
    return result
