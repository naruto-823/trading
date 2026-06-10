"""每小时仓位体检服务

数据流:持仓 → 重仓筛选 → 新闻 + web_search 调研 → Anthropic 出结构化指导
       → 落库 position_analysis_report → Bark 推摘要。
全程 fail-soft:任一步异常降级(degraded=True),绝不整轮崩。

spec: docs/superpowers/specs/2026-06-10-hourly-position-analysis-design.md
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx
from anthropic import Anthropic
from sqlalchemy.orm import Session

from app.config import settings
from app.models.position_analysis_report import PositionAnalysisReport
from app.services import fx as fx_service
from app.services.account import get_latest_account
from app.services.briefing import HTTP_HEADERS, fetch_market_context, fetch_news_for_symbol
from app.services.debate_research import gather_research
from app.services.notify import send_bark
from app.services.positions import list_positions

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是用户的**仓位体检官**——每小时给他的持仓做一次盘面体检 + 操作指导。不是风险经理,是随身教练。

【用户画像 —— 必须据此调整语气和结论】
- 自评"损失厌恶 + 易补仓 + 跑不赢纳指"。点名他的补仓冲动,但**不要无脑劝阻**;给的是纪律,不是恐吓。
- mega-cap 长仓(MSFT/GOOG/META/NVDA 等)是他的赚钱机器:用**前瞻视角 + 按方向加权**判断,**别太保守、别滞后、别反射性劝降风险**。趋势没破坏就别喊减仓。
- 偏好期权 income 策略:指导里带 covered call / cash-secured put 视角。
  **硬护栏:covered call 只能在该正股持仓 ≥100 股时提;不足 100 股或没持有,禁止建议 covered call。**
- 两可决策给出你的**独立判断**(不要"看个人风险偏好"和稀泥);纯防御性问题直接给执行动作。

【输入】账户概览 + 重仓清单(含成本/现价/占比/浮亏率/当日涨跌)+ 重仓近期新闻标题 + web_search 研究简报 + 市场背景。

【输出】严格 JSON(不要 markdown 包裹),schema:
{
  "overall_stance": "攻 | 守 | 持 —— 后跟一句话理由",
  "per_position": [
    {"symbol": "MSFT.US", "read": "1-2 句盘面解读(基于输入数据/新闻/调研)", "guidance": "具体操作指导(持/加/减/写 covered call/对冲...)", "signal": "强 | 中 | 弱"}
  ],
  "alerts": ["需要你特别注意的点,按重要度排序,可为空数组"],
  "summary": "一句话中文摘要(整体盘面 + 最关键的一个动作),≤60 字"
}

【硬规则】
1. per_position 覆盖输入的每只重仓,不要漏。
2. read / guidance 必须基于输入里的真实数据(占比、浮亏率、新闻标题、调研简报、市场背景),**不要编股价、财报日期、市占率等输入里没有的硬事实**。
3. 不要"持有观察""关注 XX 价位"这种没信息量的空话;guidance 要可执行。
4. covered call 建议严守 ≥100 股护栏(见上)。
5. summary 是要推到他手机锁屏的那句话,务必精炼、有判断、有动作。"""


def _parse_analysis_json(text: str) -> dict:
    """解析 AI 输出 JSON;失败返回 degraded 降级结构。"""
    text = (text or "").strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl > 0:
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        data = json.loads(text)
        data.setdefault("overall_stance", "")
        data.setdefault("per_position", [])
        data.setdefault("alerts", [])
        data.setdefault("summary", "")
        return data
    except json.JSONDecodeError as exc:
        logger.warning("position-analysis JSON parse 失败: %s | raw: %s", exc, text[:200])
        return {
            "overall_stance": "",
            "per_position": [],
            "alerts": ["AI 输出解析失败"],
            "summary": "⚠️ 本轮体检解析失败",
            "degraded": True,
        }


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


def _call_ai(account, heavy_positions, market_ctx, news_by_symbol, research) -> dict:
    """调 Anthropic 原生通道出体检 JSON。fail-soft:任何异常 → degraded 降级结构。"""
    if not settings.anthropic_api_key:
        return _degraded("AI 未配置")
    payload = {
        "现在时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "账户概览": {
            "净资产_HKD": getattr(account, "net_assets", None),
            "总市值_HKD": getattr(account, "market_value", None),
            "现金_HKD": getattr(account, "total_cash", None),
            "当日盈亏_HKD": getattr(account, "day_pnl", None),
            "购买力_HKD": getattr(account, "buy_power", None),
        },
        "重仓清单": heavy_positions,
        "重仓近期新闻标题": {
            sym: [n.get("title", "") for n in news] for sym, news in news_by_symbol.items()
        },
        "web_search研究简报": research or "(本轮无外部调研)",
        "市场背景": market_ctx,
    }
    try:
        client = Anthropic(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url or None,
        )
        resp = client.messages.create(
            model=settings.hourly_model(),
            max_tokens=3000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2, default=str)}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text") or "{}"
        return _parse_analysis_json(text)
    except Exception as exc:
        logger.error("position-analysis _call_ai 失败: %s", exc, exc_info=True)
        return _degraded(f"AI 调用失败: {exc}")


def _degraded(reason: str) -> dict:
    return {
        "overall_stance": "",
        "per_position": [],
        "alerts": [reason],
        "summary": f"⚠️ 本轮仓位体检降级({reason})",
        "degraded": True,
    }


def _build_push(analysis: dict, account) -> tuple[str, str]:
    """生成 Bark 标题 + 正文。每整点都推一条摘要。"""
    net = float(getattr(account, "net_assets", 0) or 0)
    day_pnl = float(getattr(account, "day_pnl", 0) or 0)
    sign = "+" if day_pnl >= 0 else ""
    title = f"📊 仓位体检 · 净资产HK${net:,.0f} 日{sign}{day_pnl:,.0f}"
    lines = [analysis.get("summary", "") or "(本轮无摘要)"]
    for a in (analysis.get("alerts") or [])[:2]:
        lines.append(f"• {a}")
    body = "\n".join(lines)[:600]
    return title, body


def _collect_market_data(heavy_positions: list[dict]) -> tuple[dict, dict]:
    """拉市场背景 + 重仓新闻。整体包在外层 try 里(调用方负责降级)。"""
    with httpx.Client(timeout=10.0, headers=HTTP_HEADERS, follow_redirects=True) as client:
        market_ctx = fetch_market_context(client)
        news_by_symbol = {
            p["symbol"]: fetch_news_for_symbol(
                p["symbol"], client, name=p.get("name"),
                limit=settings.hourly_analysis_news_per_stock,
            )
            for p in heavy_positions
        }
    return market_ctx, news_by_symbol


def generate_hourly_analysis(db: Session) -> dict:
    """每整点编排:持仓→重仓→调研→AI→落库→Bark。全程 fail-soft。"""
    generated_at = datetime.now(timezone.utc)
    positions = list_positions(db)
    account = get_latest_account(db)

    if not positions or not account:
        analysis = _degraded("暂无持仓/账户数据,请先同步")
        return _persist_and_push(db, generated_at, account, [], "", analysis, degraded=True)

    heavy = select_heavy_positions(
        positions, account, db,
        top_n=settings.hourly_analysis_top_n,
        min_pct=settings.hourly_analysis_min_position_pct,
    )

    # 市场数据(fail-soft)
    market_ctx, news_by_symbol = {}, {}
    try:
        market_ctx, news_by_symbol = _collect_market_data(heavy)
    except Exception as exc:
        logger.warning("position-analysis 市场数据降级: %s", exc)

    # web_search 调研(fail-soft;gather_research 自身永不抛,这里再兜一层)
    research = ""
    if settings.hourly_analysis_websearch_enabled:
        try:
            tickers = [p["symbol"] for p in heavy]
            content = "组合重仓体检:" + ", ".join(tickers)
            research = gather_research(content, tickers)
        except Exception as exc:
            logger.warning("position-analysis 调研降级: %s", exc)
            research = ""

    analysis = _call_ai(account, heavy, market_ctx, news_by_symbol, research)
    degraded = bool(analysis.get("degraded"))
    return _persist_and_push(db, generated_at, account, heavy, research, analysis, degraded=degraded)


def _persist_and_push(db, generated_at, account, heavy, research, analysis, degraded) -> dict:
    """落库 + Bark 推送(每整点都推)。返回报告 dict。"""
    account_json = None
    if account is not None:
        account_json = json.dumps({
            "net_assets": getattr(account, "net_assets", None),
            "market_value": getattr(account, "market_value", None),
            "total_cash": getattr(account, "total_cash", None),
            "day_pnl": getattr(account, "day_pnl", None),
        }, ensure_ascii=False)

    row = PositionAnalysisReport(
        generated_at=generated_at,
        account_json=account_json,
        positions_json=json.dumps(heavy, ensure_ascii=False, default=str),
        research_brief=research or "",
        analysis_json=json.dumps(analysis, ensure_ascii=False),
        summary=analysis.get("summary", ""),
        push_status="pending",
        degraded=degraded,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # 推送(account 为 None 时给个占位,_build_push 用 getattr 安全)
    title, body = _build_push(analysis, account)
    res = send_bark(title, body, group="position-analysis", level="active")
    row.push_status = "sent" if res.get("ok") else "failed"
    row.push_detail = str(res.get("detail"))[:500]
    db.commit()
    db.refresh(row)

    return _row_to_dict(row)


def _row_to_dict(row: PositionAnalysisReport) -> dict:
    return {
        "id": row.id,
        "generated_at": _ensure_utc(row.generated_at).isoformat() if row.generated_at else None,
        "account": json.loads(row.account_json) if row.account_json else None,
        "positions": json.loads(row.positions_json) if row.positions_json else [],
        "research_brief": row.research_brief or "",
        "analysis": json.loads(row.analysis_json) if row.analysis_json else {},
        "summary": row.summary,
        "push_status": row.push_status,
        "degraded": row.degraded,
    }


def _ensure_utc(dt: datetime) -> datetime:
    """SQLite 存的 naive datetime 当 UTC 用,输出带 +00:00 offset 防前端按本地时区误解析。"""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def get_latest_report(db: Session) -> dict | None:
    row = (
        db.query(PositionAnalysisReport)
        .order_by(PositionAnalysisReport.generated_at.desc())
        .first()
    )
    return _row_to_dict(row) if row else None


def list_report_history(db: Session, limit: int = 24) -> list[dict]:
    rows = (
        db.query(PositionAnalysisReport)
        .order_by(PositionAnalysisReport.generated_at.desc())
        .limit(limit)
        .all()
    )
    return [_row_to_dict(r) for r in rows]
