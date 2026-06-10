"""每小时仓位体检服务

数据流:持仓 → 重仓筛选 → 新闻 + web_search 调研 → Anthropic 出结构化指导
       → 落库 position_analysis_report → Bark 推摘要。
全程 fail-soft:任一步异常降级(degraded=True),绝不整轮崩。

spec: docs/superpowers/specs/2026-06-10-hourly-position-analysis-design.md
"""

from __future__ import annotations

import json
import logging
import re

from app.services import fx as fx_service

logger = logging.getLogger(__name__)


def _is_option(symbol: str) -> bool:
    # 期权合约 symbol(如 MSFT260618C440000.US):长 + 含 C/P + 含数字
    return len(symbol) > 12 and any(c in symbol for c in ["C", "P"]) and any(d.isdigit() for d in symbol)


def select_heavy_positions(positions, account, db, top_n: int, min_pct: float) -> list[dict]:
    """选重仓:占净资产% ≥ min_pct 的、按 HKD 市值降序的前 top_n 只(剔除期权)。
    没有任何仓位达标时,兜底取市值前 top_n。返回 enrich 后的 dict 列表。
    """
    net = float(getattr(account, "net_assets", 0) or 0)
    stocks = [p for p in positions if not _is_option(p.symbol)]
    enriched = []
    for p in stocks:
        hkd_mv = fx_service.to_hkd(abs(p.market_value), p.currency, db)
        pct = (hkd_mv / net * 100) if net > 0 else 0.0
        enriched.append({
            "symbol": p.symbol,
            "name": p.name,
            "数量": p.quantity,
            "成本价": p.cost_price,
            "现价": p.current_price,
            "市值": p.market_value,
            "货币": p.currency,
            "占净资产%": round(pct, 1),
            "浮动盈亏": p.unrealized_pnl,
            "浮亏率%": round(p.unrealized_pnl_ratio * 100, 1),
            "当日涨跌%": round(p.day_pnl_ratio * 100, 2),
            "_hkd_mv": hkd_mv,
        })
    enriched.sort(key=lambda d: d["_hkd_mv"], reverse=True)
    heavy = [d for d in enriched if d["占净资产%"] >= min_pct][:top_n]
    if not heavy:
        heavy = enriched[:top_n]  # 兜底:没仓位达标也别空手
    for d in heavy:
        d.pop("_hkd_mv", None)
    return heavy
