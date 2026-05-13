"""持仓客观分析服务

仅做数据层面的统计描述，不输出任何买卖建议或主观判断。
所有金额统一换算到 HKD 以便横向对比。
"""

from typing import Any

from sqlalchemy.orm import Session

from app.models.position import Position

# 与前端 / sync.py 保持一致的 fallback 汇率（实际同步时长桥会按实时汇率换算）
USD_TO_HKD_FALLBACK = 7.83

# 集中度阈值（用于生成 alerts，纯描述性，不构成建议）
SINGLE_POSITION_HIGH_PCT = 20.0
TOP3_HIGH_PCT = 60.0


def _is_option_symbol(symbol: str) -> bool:
    """判断是否为期权标的（如 MSFT260515P350000.US）"""
    parts = symbol.rsplit(".", 1)
    if len(parts) != 2:
        return False
    ticker = parts[0]
    return len(ticker) > 10 and any(c in ticker for c in "PC") and any(c.isdigit() for c in ticker[-6:])


def _to_hkd(value: float, currency: str) -> float:
    """把金额换算成 HKD"""
    if currency == "USD":
        return value * USD_TO_HKD_FALLBACK
    return value


def _safe_pct(numerator: float, denominator: float) -> float:
    if not denominator:
        return 0.0
    return round(numerator / denominator * 100, 2)


def analyze_portfolio(db: Session) -> dict[str, Any]:
    """对当前持仓进行多维度客观分析"""
    all_positions = db.query(Position).all()
    if not all_positions:
        return {
            "summary": {"position_count": 0, "total_market_value_hkd": 0.0},
            "concentration": None,
            "pnl_distribution": None,
            "cost_structure": None,
            "derivatives": None,
            "alerts": [],
            "disclaimer": "本分析仅基于账户数据进行客观统计，不构成任何投资建议。",
        }

    # 把每个仓位转成统一的 dict（含 HKD 换算）
    items: list[dict[str, Any]] = []
    for p in all_positions:
        is_option = _is_option_symbol(p.symbol)
        # 期权市值需要 ×100 multiplier；DB 里的 market_value 已经是换算后的
        # 但保险起见用绝对值，避免空头市值为负影响占比计算
        mv_hkd = _to_hkd(abs(p.market_value), p.currency)
        cost_hkd = _to_hkd(abs(p.cost_price * p.quantity) * (100 if is_option else 1), p.currency)
        pnl_hkd = _to_hkd(p.unrealized_pnl, p.currency)
        day_pnl_hkd = _to_hkd(p.day_pnl, p.currency)

        items.append({
            "symbol": p.symbol,
            "name": p.name,
            "market": p.market,
            "currency": p.currency,
            "quantity": p.quantity,
            "is_option": is_option,
            "is_short": p.quantity < 0,
            "market_value_hkd": round(mv_hkd, 2),
            "cost_value_hkd": round(cost_hkd, 2),
            "unrealized_pnl_hkd": round(pnl_hkd, 2),
            "unrealized_pnl_ratio_pct": round(p.unrealized_pnl_ratio * 100, 2),
            "day_pnl_hkd": round(day_pnl_hkd, 2),
            "day_pnl_ratio_pct": round(p.day_pnl_ratio * 100, 2),
        })

    total_mv_hkd = sum(i["market_value_hkd"] for i in items) or 0.0

    return {
        "summary": _build_summary(items, total_mv_hkd),
        "concentration": _build_concentration(items, total_mv_hkd),
        "pnl_distribution": _build_pnl_distribution(items),
        "cost_structure": _build_cost_structure(items, total_mv_hkd),
        "derivatives": _build_derivatives(items, total_mv_hkd),
        "alerts": _build_alerts(items, total_mv_hkd),
        "disclaimer": (
            "本分析仅基于账户当前持仓数据进行客观统计描述，"
            "所有数字均为事实陈述，不包含任何买卖建议或市场预测。"
            "投资决策请结合自身风险偏好独立判断。"
        ),
    }


def _build_summary(items: list[dict], total_mv_hkd: float) -> dict[str, Any]:
    by_currency: dict[str, float] = {}
    by_market: dict[str, float] = {}
    for i in items:
        by_currency[i["currency"]] = by_currency.get(i["currency"], 0.0) + i["market_value_hkd"]
        by_market[i["market"]] = by_market.get(i["market"], 0.0) + i["market_value_hkd"]

    return {
        "position_count": len(items),
        "total_market_value_hkd": round(total_mv_hkd, 2),
        "currency_breakdown": [
            {"currency": cur, "market_value_hkd": round(mv, 2), "pct": _safe_pct(mv, total_mv_hkd)}
            for cur, mv in sorted(by_currency.items(), key=lambda x: -x[1])
        ],
        "market_breakdown": [
            {"market": mk, "market_value_hkd": round(mv, 2), "pct": _safe_pct(mv, total_mv_hkd)}
            for mk, mv in sorted(by_market.items(), key=lambda x: -x[1])
        ],
    }


def _build_concentration(items: list[dict], total_mv_hkd: float) -> dict[str, Any]:
    sorted_items = sorted(items, key=lambda x: -x["market_value_hkd"])
    top_holdings = [
        {
            "symbol": i["symbol"],
            "name": i["name"],
            "market_value_hkd": i["market_value_hkd"],
            "pct": _safe_pct(i["market_value_hkd"], total_mv_hkd),
        }
        for i in sorted_items[:5]
    ]
    top1_pct = top_holdings[0]["pct"] if top_holdings else 0.0
    top3_pct = sum(h["pct"] for h in top_holdings[:3])
    top5_pct = sum(h["pct"] for h in top_holdings[:5])

    return {
        "top_holdings": top_holdings,
        "top1_pct": round(top1_pct, 2),
        "top3_pct": round(top3_pct, 2),
        "top5_pct": round(top5_pct, 2),
    }


def _build_pnl_distribution(items: list[dict]) -> dict[str, Any]:
    profitable = [i for i in items if i["unrealized_pnl_hkd"] > 0]
    losing = [i for i in items if i["unrealized_pnl_hkd"] < 0]
    flat = [i for i in items if i["unrealized_pnl_hkd"] == 0]

    biggest_winner = max(items, key=lambda x: x["unrealized_pnl_hkd"], default=None)
    biggest_loser = min(items, key=lambda x: x["unrealized_pnl_hkd"], default=None)

    today_winners = sorted(
        [i for i in items if i["day_pnl_hkd"] > 0],
        key=lambda x: -x["day_pnl_hkd"],
    )[:3]
    today_losers = sorted(
        [i for i in items if i["day_pnl_hkd"] < 0],
        key=lambda x: x["day_pnl_hkd"],
    )[:3]

    total_pnl = sum(i["unrealized_pnl_hkd"] for i in items)
    total_day_pnl = sum(i["day_pnl_hkd"] for i in items)

    return {
        "total_unrealized_pnl_hkd": round(total_pnl, 2),
        "total_day_pnl_hkd": round(total_day_pnl, 2),
        "profitable_count": len(profitable),
        "losing_count": len(losing),
        "flat_count": len(flat),
        "win_rate_pct": _safe_pct(len(profitable), len(items)),
        "biggest_winner": _summarize_pos(biggest_winner) if biggest_winner else None,
        "biggest_loser": _summarize_pos(biggest_loser) if biggest_loser else None,
        "today_top_winners": [_summarize_pos(i, day=True) for i in today_winners],
        "today_top_losers": [_summarize_pos(i, day=True) for i in today_losers],
    }


def _summarize_pos(i: dict, day: bool = False) -> dict[str, Any]:
    base = {"symbol": i["symbol"], "name": i["name"]}
    if day:
        base["day_pnl_hkd"] = i["day_pnl_hkd"]
        base["day_pnl_ratio_pct"] = i["day_pnl_ratio_pct"]
    else:
        base["unrealized_pnl_hkd"] = i["unrealized_pnl_hkd"]
        base["unrealized_pnl_ratio_pct"] = i["unrealized_pnl_ratio_pct"]
    return base


def _build_cost_structure(items: list[dict], total_mv_hkd: float) -> dict[str, Any]:
    total_cost_hkd = sum(i["cost_value_hkd"] for i in items) or 0.0
    structure = []
    for i in items:
        cost_pct = _safe_pct(i["cost_value_hkd"], total_cost_hkd)
        mv_pct = _safe_pct(i["market_value_hkd"], total_mv_hkd)
        structure.append({
            "symbol": i["symbol"],
            "name": i["name"],
            "cost_pct": cost_pct,
            "market_value_pct": mv_pct,
            "weight_drift_pct": round(mv_pct - cost_pct, 2),  # 正：涨出来的权重
        })
    return {
        "total_cost_hkd": round(total_cost_hkd, 2),
        "items": sorted(structure, key=lambda x: -abs(x["weight_drift_pct"])),
    }


def _build_derivatives(items: list[dict], total_mv_hkd: float) -> dict[str, Any]:
    options = [i for i in items if i["is_option"]]
    if not options:
        return {"option_count": 0, "option_market_value_hkd": 0.0, "option_pct": 0.0}

    long_options = [i for i in options if not i["is_short"]]
    short_options = [i for i in options if i["is_short"]]
    option_mv = sum(i["market_value_hkd"] for i in options)

    return {
        "option_count": len(options),
        "option_market_value_hkd": round(option_mv, 2),
        "option_pct": _safe_pct(option_mv, total_mv_hkd),
        "long_count": len(long_options),
        "short_count": len(short_options),
        "long_options": [
            {"symbol": i["symbol"], "quantity": i["quantity"], "market_value_hkd": i["market_value_hkd"]}
            for i in long_options
        ],
        "short_options": [
            {"symbol": i["symbol"], "quantity": i["quantity"], "market_value_hkd": i["market_value_hkd"]}
            for i in short_options
        ],
    }


def _build_alerts(items: list[dict], total_mv_hkd: float) -> list[dict[str, str]]:
    """生成客观风险描述（不是建议，只是数字达到阈值时的提示）"""
    alerts: list[dict[str, str]] = []
    if not items or total_mv_hkd <= 0:
        return alerts

    sorted_items = sorted(items, key=lambda x: -x["market_value_hkd"])
    top1_pct = _safe_pct(sorted_items[0]["market_value_hkd"], total_mv_hkd)
    if top1_pct >= SINGLE_POSITION_HIGH_PCT:
        alerts.append({
            "level": "info",
            "type": "single_concentration",
            "message": f"{sorted_items[0]['symbol']} 占总市值 {top1_pct}%，超过 {SINGLE_POSITION_HIGH_PCT}% 阈值",
        })

    top3_pct = sum(_safe_pct(i["market_value_hkd"], total_mv_hkd) for i in sorted_items[:3])
    if top3_pct >= TOP3_HIGH_PCT:
        alerts.append({
            "level": "info",
            "type": "top3_concentration",
            "message": f"前三大持仓合计占 {round(top3_pct, 2)}%，超过 {TOP3_HIGH_PCT}% 阈值",
        })

    # 单一货币暴露
    by_currency: dict[str, float] = {}
    for i in items:
        by_currency[i["currency"]] = by_currency.get(i["currency"], 0.0) + i["market_value_hkd"]
    for cur, mv in by_currency.items():
        pct = _safe_pct(mv, total_mv_hkd)
        if pct >= 80.0:
            alerts.append({
                "level": "info",
                "type": "currency_concentration",
                "message": f"{cur} 资产占 {pct}%，外汇敞口集中",
            })

    # 期权空头敞口
    short_options = [i for i in items if i["is_option"] and i["is_short"]]
    if short_options:
        short_mv = sum(i["market_value_hkd"] for i in short_options)
        alerts.append({
            "level": "info",
            "type": "short_options",
            "message": f"持有 {len(short_options)} 个期权空头仓位，对应市值 {round(short_mv, 2)} HKD",
        })

    return alerts
