"""Deterministic portfolio risk calculations; no model calls or market-data writes."""

from __future__ import annotations

import re
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.services import fx as fx_service
from app.services.account import get_latest_account
from app.services.positions import list_positions

_OPTION = re.compile(r"^([A-Z0-9.]+)(\d{6})([CP])(\d+)\.US$")


def _option_details(symbol: str) -> dict | None:
    match = _OPTION.match(symbol.upper())
    if not match:
        return None
    underlying, expiry_raw, kind, strike_raw = match.groups()
    try:
        expiry = datetime.strptime(expiry_raw, "%y%m%d").date()
    except ValueError:
        return None
    return {
        "underlying": underlying,
        "expiry": expiry.isoformat(),
        "days_to_expiry": (expiry - date.today()).days,
        "kind": "call" if kind == "C" else "put",
        "strike": int(strike_raw) / 1000,
    }


def build_risk_radar(db: Session, shock_pct: float = 10.0) -> dict:
    """Return explainable concentration, leverage, option and shock risk in HKD."""
    account = get_latest_account(db)
    positions = list_positions(db)
    if account is None:
        return {"as_of": None, "currency": "HKD", "summary": [], "concentration": [], "options": [], "stress": None}

    stock_values: dict[str, float] = {}
    stock_quantities: dict[str, int] = {}
    options: list[dict] = []
    gross_stock_value_hkd = 0.0
    for position in positions:
        detail = _option_details(position.symbol)
        value_hkd = fx_service.to_hkd(position.market_value, position.currency, db)
        if detail is not None:
            detail.update({
                "symbol": position.symbol,
                "quantity": position.quantity,
                "market_value_hkd": round(value_hkd, 2),
                "covered": False,
                "risk_flags": [],
            })
            options.append(detail)
            continue
        stock_values[position.symbol] = stock_values.get(position.symbol, 0.0) + value_hkd
        stock_quantities[position.symbol] = stock_quantities.get(position.symbol, 0) + position.quantity
        gross_stock_value_hkd += abs(value_hkd)

    for option in options:
        if option["kind"] == "call" and option["quantity"] < 0:
            option["covered"] = stock_quantities.get(f"{option['underlying']}.US", 0) >= abs(option["quantity"]) * 100
            if not option["covered"]:
                option["risk_flags"].append("裸卖 Call：没有足额正股覆盖")
        if option["days_to_expiry"] <= 7:
            option["risk_flags"].append("7 日内到期：时间价值和行权风险加速")
        elif option["days_to_expiry"] <= 21:
            option["risk_flags"].append("21 日内到期：关注展期或止盈计划")
        if option["quantity"] < 0 and option["kind"] == "put":
            option["risk_flags"].append("卖出 Put：需确认现金/购买力覆盖")

    net_assets = float(account.net_assets or 0)
    denominator = abs(net_assets) or gross_stock_value_hkd or 1.0
    concentration = []
    for symbol, value in stock_values.items():
        weight = abs(value) / denominator
        risk_level = "high" if weight >= 0.25 else "medium" if weight >= 0.12 else "normal"
        concentration.append({
            "symbol": symbol,
            "market_value_hkd": round(value, 2),
            "weight_pct": round(weight * 100, 2),
            "risk_level": risk_level,
        })
    concentration.sort(key=lambda row: abs(row["market_value_hkd"]), reverse=True)

    shock = -abs(float(shock_pct)) / 100
    stock_shock_hkd = sum(value * shock for value in stock_values.values())
    # Options are deliberately excluded: without Greeks, treating their value as linear is misleading.
    summary: list[dict] = []
    if concentration and concentration[0]["weight_pct"] >= 25:
        summary.append({
            "level": "high",
            "title": "单一持仓集中",
            "detail": f"{concentration[0]['symbol']} 占净资产 {concentration[0]['weight_pct']:.1f}%",
        })
    debt = abs(float(account.outstanding_debt or 0))
    if debt > 0:
        summary.append({
            "level": "high" if debt / denominator >= 0.2 else "medium",
            "title": "融资敞口",
            "detail": f"融资欠款 HK${debt:,.0f}，约为净资产 {debt / denominator * 100:.1f}%",
        })
    maintenance = float(account.maintenance_margin or 0)
    if maintenance > 0:
        summary.append({
            "level": "high" if maintenance / denominator >= 0.8 else "medium",
            "title": "维持保证金",
            "detail": f"维持保证金占净资产 {maintenance / denominator * 100:.1f}%",
        })
    if any(item["risk_flags"] for item in options):
        summary.append({
            "level": "medium",
            "title": "期权需处理",
            "detail": f"{sum(bool(item['risk_flags']) for item in options)} 个期权仓位有到期或覆盖风险",
        })
    if not summary:
        summary.append({
            "level": "normal",
            "title": "暂无硬性风险触发",
            "detail": "仍应关注单一行业相关性和期权 Greeks。",
        })

    return {
        "as_of": account.synced_at.isoformat(),
        "currency": "HKD",
        "summary": summary,
        "concentration": concentration,
        "options": options,
        "stress": {
            "shock_pct": round(shock * 100, 2),
            "estimated_stock_pnl_hkd": round(stock_shock_hkd, 2),
            "estimated_net_assets_hkd": round(net_assets + stock_shock_hkd, 2),
            "excluded": "期权未纳入线性压力测试；需实时 Greeks 才能可靠估算。",
        },
    }
