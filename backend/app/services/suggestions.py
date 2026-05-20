"""AI 决策建议服务

跟 briefing 区别：briefing 是"今日复盘"，suggestions 是"具体可执行的交易建议"。
- 复用 briefing 的数据采集（市场背景 + 新闻）
- 用更激进的 prompt：必须输出可执行动作（stop_loss / sell / buy / add），不要"持有观察"
- 加入用户画像：损失厌恶、容易补仓、跑不赢纳指、当前融资透支
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from anthropic import Anthropic
from sqlalchemy.orm import Session

from app.config import settings
from app.models.suggestion import Suggestion as SuggestionRow
from app.services import fx as fx_service
from app.services import suggestion_debate
from app.services.account import get_latest_account
from app.services.briefing import (
    HTTP_HEADERS,
    fetch_market_context,
    fetch_news_for_symbol,
)
from app.services.positions import list_positions
from app.services.yahoo_quote import fetch_yahoo_quotes

logger = logging.getLogger(__name__)

# 杠杆 ETF / 期权识别（跟前端 positionRules.ts 保持口径一致）
LEVERAGED_KEYWORDS = ["2x", "3x", "Bull", "Bear", "Leveraged", "Daily Long", "Daily Short"]


def _is_leveraged(name: str) -> bool:
    n = (name or "").lower()
    return any(k.lower() in n for k in LEVERAGED_KEYWORDS)


def _is_option(symbol: str) -> bool:
    # MSFT260618C440000.US 这种格式
    return len(symbol) > 12 and any(c in symbol for c in ["C", "P"]) and any(d.isdigit() for d in symbol)


SYSTEM_PROMPT = """你是用户的**进攻型交易策略师**，不是风险经理。任务：从他账户和当前市场里挖出**没在用的 alpha**，主动给方向。

【用户画像】
- 自评"损失厌恶 + 易补仓 + 跑不赢纳指"——你的工作是帮他**克服**这些倾向，不是迎合
- 他不要兜底人，要教练 + 策略师
- 能接受波动，但讨厌**无聊的"持有观察"**和过度保守的"现金留着防御"
- 已签知情免责声明，可以直接给方向性建议

【你必须主动挖掘的机会类型】（不要只看持仓改不改）
1. **期权 income 策略**：基于现有正股写 covered call、用现金担保 put（用户做过 META 590P 现在盈利 +52%，明显有这方面经验）
2. **新建仓**：基于市场背景 + 新闻发现的**新机会**（不必是用户已有的标的）
3. **主题轮动**：基于输入新闻 + 宏观数据的实际证据，不要凭空列 ticker
4. **利率环境利用**：当输入显示 10y 美债收益率高位时，可考虑 TLT/IEF 类
5. **强势股加仓**（不是补仓亏损股）：基于新 catalyst 的强势标的逢低进场
6. **载体替换**：杠杆 ETF → 正股；ITM call → 正股+covered call；等结构性优化

【输出格式】严格 JSON（不要 markdown 包裹），schema：

{
  "summary": "整体策略一句话（如：清掉 1 个结构问题，3 个新仓建议 + 2 个 income 策略）",
  "suggestions": [
    {
      "action": "stop_loss" | "sell" | "buy" | "add",
      "symbol": "TLT.US",
      "qty": "20",
      "price": "约 89.5",
      "urgency": "high" | "medium" | "low",
      "thesis": "1-2 句话核心理由：为什么这个动作，为什么是现在",
      "data_points": ["具体数据点 1", "具体数据点 2", "具体数据点 3"]
    }
  ]
}

【data_points 严格规则 — 这是最重要的一条】
- **每一条 data_point 必须直接论证该建议的 thesis，且能用一句话连起来：「因为 [data_point]，所以 [thesis 的 X 部分]」**
- 输入新闻按 symbol 分组喂给你，**每条新闻只能用于跟该 symbol 直接相关的建议的 thesis**
  - 反面例子：用 "META 7000 员工转 AI" 论证买 MU——MU 的需求来自数据中心 HBM 销售，跟 META 内部组织调整无任何因果链。**这种跨标的"主题联想"严禁出现**
  - 正面例子：买 GOOG 时引用 "GOOG-Blackstone 50亿 TPU 云合资" — 同一个 symbol 的新闻支撑该 symbol 的 thesis
- **禁止主题词当 data point**："AI 基建主题轮动窗口" / "估值有空间" 这种**没有数字、没有事件、没有引用源**的措辞**严禁**
- 每个 data_point 必须是下列之一：
  - 具体百分比 / 美元金额（来自输入持仓 / 账户 / 市场背景）
  - 一条输入里有的新闻标题（带 symbol 归属）
  - 输入里给的具体宏观数字（原油价 / 指数涨跌幅 / 期货价等）

【硬规则】
1. **总数 5-8 条**，结构必须大致平衡：
   - 风险/防御类（stop_loss + sell + 还债）：**最多 2 条**，只保留高确信度结构性问题（如杠杆 ETF）
   - 进攻类（buy + 期权 income + 主题）：**至少 3 条**
2. **不允许"持有观察""关注 XX 价位"**这种没动作的空话
3. **不要为损失厌恶背书**：不要"组合健康继续持有"、不要"现金保留观望"
4. **不推荐 add（补仓亏损股）**——但 buy 新仓（即使是用户已持有的标的）可以推荐
5. thesis 必须基于具体数据，不能是"看好长期"
6. urgency: high（本周）、medium（本月）、low（中长期）
7. 涉及融资透支时，可以建议"用 X 的卖出资金还透支"，但**别让"还透支"占走超过 1 条建议**
8. **不要在思路里出现具体 ticker example 来源于本 prompt**——所有推荐标的必须来自：用户持仓 / 输入新闻提及的 symbol / 输入市场背景的 ETF 代码

【仓位口径】
- 输入字段 `占净资产%` 是该仓位 HKD 市值 ÷ 账户净资产（含现金）。集中度判断以此为准（>20% 算单一过重，>50% 算前 3 集中）
- 不要把"持仓在 holdings 里的占比"（不含现金）当成"占组合"——会高估集中度

【价格与事实声明 — 极其重要】
- 输入的【持仓清单】里有标的的成本价和现价；【市场背景】里有指数 / 期货 / 原油等价格。**这些以外的标的价格你不知道。**
- 对**输入数据里没现价的标的**：**price 字段写 "查询当前价" 或价格区间描述**（如"近 1 月支撑区"），**不要编具体美元数字**
- 对期权建议：可以给行权价 + 到期日，但权利金价格写"市价"或"参考实时报价"，不要编数字
- 对市场容量 / 增长率 / 财报日期 / 营收数字等**事实声明**：除非输入里有，否则不要编；可以说"参考最新季报"
- 系统会自动校准已知美股的价格，AI 编的离谱会被标红覆盖 — 别想蒙

【明令禁止】
- 不要"组合整体健康"、"继续持有核心仓位"这种废话
- 不要把所有建议都集中在"卖出 + 止损"
- 不要不识别 covered call / 现金担保 put 等 income strategy，对这些不要建议平仓
- **不要编具体股价、HBM 市场规模、财报日期、市占率数字等"硬事实"**——除非输入数据里有
- **不要跨标的拼凑 data_points**（META 新闻 → MU 论据这种关联谬误）"""


def _enrich_positions(positions, pct_denom_hkd: float, db: Session | None = None) -> list[dict]:
    """给每只持仓加上：占净资产 %、是否杠杆、是否期权

    pct_denom_hkd: 占比分母（账户净资产 HKD）。跟 briefing / portfolio_analysis 统一口径。
    """
    enriched = []
    for p in positions:
        hkd_mv = fx_service.to_hkd(abs(p.market_value), p.currency, db)
        ratio = hkd_mv / pct_denom_hkd if pct_denom_hkd > 0 else 0
        enriched.append({
            "symbol": p.symbol,
            "name": p.name,
            "数量": p.quantity,
            "成本价": p.cost_price,
            "现价": p.current_price,
            "市值": p.market_value,
            "货币": p.currency,
            "占净资产%": round(ratio * 100, 1),
            "浮动盈亏": p.unrealized_pnl,
            "浮亏率%": round(p.unrealized_pnl_ratio * 100, 1),
            "当日涨跌%": round(p.day_pnl_ratio * 100, 2),
            "是否杠杆ETF": _is_leveraged(p.name),
            "是否期权": _is_option(p.symbol),
        })
    return enriched


def build_suggestions(db: Session, force_refresh: bool = False) -> dict:
    """生成 AI 决策建议。

    持久化策略:每次新生成的一批入库(共享 batch_id + generated_at)。
    force_refresh=False:只读 DB 里 worker 产出的最新批次,绝不内联重算
    (freshness 由 suggestions_worker 负责)。
    force_refresh=True:完整重算(Opus 批量 + 辩论复核),供 worker / 用户手动刷新用。
    """
    positions = list_positions(db)
    account = get_latest_account(db)

    if not positions or not account:
        return _empty_response("暂无持仓数据，请先同步")

    # 按需读取:只返回 worker 产出的最新批次,绝不内联重算(Phase 2)。
    # freshness 由 suggestions_worker 负责;force_refresh=True 才完整重算。
    if not force_refresh:
        latest_batch = _load_latest_batch(db)
        if latest_batch is None:
            return _empty_response("建议尚未生成,等下次定时刷新或手动刷新")
        _, rows = latest_batch
        return _batch_to_response(rows, cache_hit=True)

    # 占比分母 = 净资产（含现金），跟 briefing / portfolio_analysis 统一口径
    pct_denom_hkd = float(account.net_assets or 0)
    if pct_denom_hkd <= 0:
        # 兜底：账户没快照时用持仓总市值
        pct_denom_hkd = sum(fx_service.to_hkd(abs(p.market_value), p.currency, db) for p in positions)

    # 抓市场背景 + 重仓股新闻（前 6 大）
    sorted_pos = sorted(positions, key=lambda p: abs(p.market_value), reverse=True)
    news_targets = [p for p in sorted_pos if not _is_option(p.symbol)][:6]

    with httpx.Client(timeout=10.0, headers=HTTP_HEADERS, follow_redirects=True) as client:
        market_ctx = fetch_market_context(client)
        news_by_symbol = {
            p.symbol: fetch_news_for_symbol(p.symbol, client, name=p.name, limit=3)
            for p in news_targets
        }

    enriched_positions = _enrich_positions(positions, pct_denom_hkd, db)

    # 已识别的账户问题（喂给 LLM 作为强提示）
    known_issues = _identify_issues(account, enriched_positions)

    if settings.anthropic_api_key:
        result = _call_opus(account, enriched_positions, market_ctx, news_by_symbol, known_issues)
    else:
        result = _mock_response("AI 未配置")

    # 实价校准：AI 对未持有标的常会编价格（用训练数据的旧价），抓 Nasdaq 实价比对
    held_symbols = {p["symbol"] for p in enriched_positions}
    _verify_prices(result.get("suggestions", []), held_symbols)

    # 购买力检查：超过可用资金的 buy 建议要标黄/标红
    _check_affordability(
        result.get("suggestions", []),
        buy_power_hkd=account.buy_power or 0,
        held_positions=enriched_positions,
    )

    # 辩论复核(Phase 2):对每条建议跑看多/看空辩论,矛盾→标注+降 urgency。
    # 包 try/except —— 辩论失败也不能丢 Opus 建议。
    try:
        suggestion_debate.debate_batch(result.get("suggestions", []))
    except Exception as exc:
        logger.warning("debate_batch 失败,落库未复核批次: %s", exc)

    # 持久化这一批
    batch_id = uuid.uuid4().hex
    generated_at = datetime.now(timezone.utc)
    rows = _persist_batch(db, batch_id, generated_at, result.get("summary", ""), result.get("suggestions", []))
    return _batch_to_response(rows, cache_hit=False)


def _load_latest_batch(db: Session) -> tuple[datetime, list[SuggestionRow]] | None:
    """拿最新一批 suggestions（按 generated_at 倒序找第一批）"""
    latest = db.query(SuggestionRow).order_by(SuggestionRow.generated_at.desc()).first()
    if not latest:
        return None
    rows = (
        db.query(SuggestionRow)
        .filter(SuggestionRow.batch_id == latest.batch_id)
        .order_by(SuggestionRow.row_id.asc())
        .all()
    )
    return latest.generated_at, rows


def _persist_batch(
    db: Session,
    batch_id: str,
    generated_at: datetime,
    summary: str,
    suggestions: list[dict],
) -> list[SuggestionRow]:
    rows: list[SuggestionRow] = []
    for s in suggestions:
        row = SuggestionRow(
            row_id=uuid.uuid4().hex,
            batch_id=batch_id,
            generated_at=generated_at,
            summary=summary,
            suggestion_key=s.get("id") or f"{s.get('symbol', '')}-{s.get('action', '')}",
            action=s.get("action", ""),
            symbol=s.get("symbol", ""),
            qty=s.get("qty", ""),
            price=s.get("price", ""),
            urgency=s.get("urgency", "medium"),
            thesis=s.get("thesis", ""),
            data_points_json=json.dumps(s.get("data_points", []), ensure_ascii=False),
            affordability_json=(
                json.dumps(s["affordability"], ensure_ascii=False)
                if s.get("affordability") else None
            ),
            debate_json=(
                json.dumps(s["debate"], ensure_ascii=False) if s.get("debate") else None
            ),
        )
        db.add(row)
        rows.append(row)
    db.commit()
    for r in rows:
        db.refresh(r)
    return rows


def _row_to_dict(row: SuggestionRow) -> dict:
    return {
        "id": row.suggestion_key,
        "row_id": row.row_id,
        "action": row.action,
        "symbol": row.symbol,
        "qty": row.qty,
        "price": row.price,
        "urgency": row.urgency,
        "thesis": row.thesis,
        "data_points": json.loads(row.data_points_json) if row.data_points_json else [],
        "affordability": json.loads(row.affordability_json) if row.affordability_json else None,
        "debate": json.loads(row.debate_json) if row.debate_json else None,
        "dismissed": row.dismissed_at is not None,
        "adopted_decision_id": row.adopted_decision_id,
    }


def _batch_to_response(rows: list[SuggestionRow], cache_hit: bool) -> dict:
    if not rows:
        return _empty_response("暂无建议")
    first = rows[0]
    # 默认前端只展示未驳回的（保持原 UX），但 row_id + dismissed flag 都传过去
    return {
        "generated_at": _ensure_utc(first.generated_at).isoformat(),
        "cache_hit": cache_hit,
        "batch_id": first.batch_id,
        "summary": first.summary,
        "suggestions": [_row_to_dict(r) for r in rows if r.dismissed_at is None],
    }


def _ensure_utc(dt: datetime) -> datetime:
    """SQLite 存的 naive datetime 当 UTC 用"""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def dismiss_suggestion(db: Session, row_id: str) -> bool:
    row = db.get(SuggestionRow, row_id)
    if not row:
        return False
    if row.dismissed_at is None:
        row.dismissed_at = datetime.now(timezone.utc)
        db.commit()
    return True


def mark_suggestion_adopted(db: Session, suggestion_key: str, decision_id: str) -> None:
    """create_decision 时若带 source_suggestion_id，回写到最新匹配的 suggestion 上"""
    row = (
        db.query(SuggestionRow)
        .filter(SuggestionRow.suggestion_key == suggestion_key)
        .filter(SuggestionRow.adopted_decision_id.is_(None))
        .order_by(SuggestionRow.generated_at.desc())
        .first()
    )
    if row:
        row.adopted_decision_id = decision_id
        db.commit()


def list_suggestion_history(db: Session, days: int = 7) -> list[dict]:
    """按 batch 分组返回历史，每个 batch 含其下所有 suggestions（含已驳回 / 已采纳）。
    days 限定时间窗。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(SuggestionRow)
        .filter(SuggestionRow.generated_at >= cutoff)
        .order_by(SuggestionRow.generated_at.desc(), SuggestionRow.row_id.asc())
        .all()
    )
    batches: dict[str, dict] = {}
    for r in rows:
        b = batches.setdefault(r.batch_id, {
            "batch_id": r.batch_id,
            "generated_at": _ensure_utc(r.generated_at).isoformat(),
            "summary": r.summary,
            "suggestions": [],
        })
        b["suggestions"].append(_row_to_dict(r))
    return list(batches.values())


def _identify_issues(account, positions: list[dict]) -> list[str]:
    """规则识别的明显账户问题，喂给 LLM"""
    issues = []
    debt = account.outstanding_debt or 0
    if debt < 0:
        usd_hkd = fx_service.usd_to_hkd()
        debt_usd = abs(debt) / usd_hkd
        issues.append(
            f"USD 账户透支 ${debt_usd:.0f}（HK${abs(debt):.0f}），融资利率 5-6%，每年息差损失 ~${debt_usd*0.05:.0f}"
        )

    for p in positions:
        if p["是否杠杆ETF"] and p["浮亏率%"] < -15:
            issues.append(
                f"{p['symbol']} 是 2x 杠杆 ETF 且浮亏 {p['浮亏率%']}%，有 volatility decay，长持会持续掉价"
            )
        if not p["是否杠杆ETF"] and not p["是否期权"] and p["占净资产%"] > 20:
            issues.append(f"{p['symbol']} 占净资产 {p['占净资产%']}%，单股集中度过高（>20%）")
        if p["浮亏率%"] < -25 and abs(p["市值"]) < 5000:  # 小仓位大亏，鸡肋
            issues.append(f"{p['symbol']} 浮亏 {p['浮亏率%']}%，仓位小但情绪占用大")

    return issues


def _call_opus(account, positions, market_ctx, news_by_symbol, known_issues) -> dict:
    client = Anthropic(
        api_key=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
    )

    payload = {
        "现在时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "账户概览": {
            "净资产_HKD": account.net_assets,
            "总市值_HKD": account.market_value,
            "现金_HKD": account.total_cash,
            "总盈亏_HKD": account.total_pnl,
            "当日盈亏_HKD": account.day_pnl,
            "融资欠款_HKD": account.outstanding_debt,
            "USD现金": next((c.available for c in account.cash_infos if c.currency == "USD"), 0),
            "HKD现金": next((c.available for c in account.cash_infos if c.currency == "HKD"), 0),
            "购买力_HKD": account.buy_power,
            "剩余融资额度_HKD": account.remaining_finance_amount,
            "维持保证金占净资产": (
                account.maintenance_margin / account.net_assets if account.net_assets > 0 else 0
            ),
        },
        "市场背景": market_ctx,
        "持仓清单": positions,
        "重仓股最近新闻标题": {
            sym: [n["title"] for n in news] for sym, news in news_by_symbol.items()
        },
        "规则识别的问题": known_issues,
    }
    user_content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)

    try:
        resp = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=3000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        text_parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        text = "".join(text_parts) or "{}"
        return _parse_json(text)
    except Exception as exc:
        logger.error("Opus suggestions failed: %s", exc, exc_info=True)
        return _mock_response(f"AI 调用失败：{exc}")


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl > 0:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        data = json.loads(text)
        # 规范化：suggestions 数组里每条加个稳定 id（symbol+action+hash），方便前端 dismiss
        for s in data.get("suggestions", []):
            base = f"{s.get('symbol', '')}-{s.get('action', '')}"
            s["id"] = base
        return data
    except json.JSONDecodeError as exc:
        logger.warning("Suggestions JSON parse failed: %s | raw: %s", exc, text[:200])
        return {
            "summary": "AI 输出解析失败，请刷新重试",
            "suggestions": [],
            "_raw": text[:500],
        }


_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _extract_first_number(text: str) -> float | None:
    """从 'about $89.5' / '~98' / '约 264 市价' 这种字符串里抠第一个数字"""
    if not text:
        return None
    m = _NUM_RE.search(str(text))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _verify_prices(suggestions: list[dict], held_symbols: set[str]) -> None:
    """对 buy 类建议、且标的不在已持仓里的，抓实时 Nasdaq 报价校准。
    AI 报价偏差 > 15% 时直接重写 price 字段 + 在 data_points 前插一条红色警告。
    HK 标的（.HK）目前不支持，跳过校准。原地修改 suggestions。
    """
    targets = []
    for s in suggestions:
        if s.get("action") != "buy":
            continue
        sym = s.get("symbol", "")
        if sym in held_symbols:
            continue
        if not sym.endswith(".US"):
            continue
        targets.append(sym)

    if not targets:
        return

    try:
        quotes = fetch_yahoo_quotes(targets, with_extended=False)
    except Exception as exc:
        logger.warning("Price verification fetch failed: %s", exc)
        return

    for s in suggestions:
        sym = s.get("symbol", "")
        q = quotes.get(sym)
        if not q:
            continue
        real_price = q.get("regular_market_price") or q.get("post_market_price") or 0
        if real_price <= 0:
            continue

        ai_price = _extract_first_number(s.get("price", ""))
        dp = list(s.get("data_points", []))

        if ai_price is None:
            # AI 没给具体价（按修订后 prompt 期望的行为），只附实时价做参考
            s["price"] = f"实时 ${real_price:.2f}"
            continue

        deviation = abs(ai_price - real_price) / real_price
        if deviation > 0.15:
            # 价格大偏差时，AI 算的 qty 大概率也错（按 AI 旧价算的总金额）
            ai_qty = _extract_first_number(s.get("qty", ""))
            qty_hint = ""
            if ai_qty and ai_price > 0:
                ai_total = ai_qty * ai_price
                suggested_qty = max(1, int(ai_total / real_price))
                qty_hint = (
                    f" qty 建议按相同金额 ${ai_total:.0f} 调整为 ~{suggested_qty} 股"
                    f"（AI 原 qty={int(ai_qty)} 基于错误价 ${ai_price:.2f} 算出）"
                )
            warning = (
                f"⚠️ 价格校准：AI 报价 ${ai_price:.2f}，实际 ${real_price:.2f}"
                f"（偏差 {deviation*100:.0f}%）。{qty_hint}"
            )
            s["price"] = f"实时 ${real_price:.2f}（AI 原报价 ${ai_price:.2f} 偏差大，已修正）"
            s["data_points"] = [warning] + dp
        else:
            # 偏差小，加注实时价方便用户参考
            s["price"] = f"{s.get('price', '')} · 实时 ${real_price:.2f}"


def _check_affordability(
    suggestions: list[dict],
    buy_power_hkd: float,
    held_positions: list[dict],
) -> None:
    """对 buy 类正股建议核算成本，超出购买力阈值的加 affordability 字段。
    期权类（短 symbol > 12 字符）和已持有标的（看作浮盈调仓）跳过——这些场景不是"花钱买"的语义。
    原地修改 suggestions。
    """
    held = {p["symbol"]: p for p in held_positions}

    for s in suggestions:
        if s.get("action") != "buy":
            continue
        sym = s.get("symbol", "")
        if _is_option(sym):
            continue

        qty = _extract_first_number(s.get("qty", ""))
        price = _extract_first_number(s.get("price", ""))
        if not qty or not price or price <= 0:
            continue

        # 估算成本（HKD）
        cost_native = qty * price
        currency = "USD" if sym.endswith(".US") else "HKD"
        cost_hkd = fx_service.to_hkd(cost_native, currency)

        if buy_power_hkd <= 0:
            continue
        ratio = cost_hkd / buy_power_hkd

        if ratio < 0.5:
            status = "ok"
        elif ratio < 1.0:
            status = "tight"
        else:
            status = "over"

        s["affordability"] = {
            "status": status,
            "cost_hkd": round(cost_hkd, 0),
            "buy_power_hkd": round(buy_power_hkd, 0),
            "ratio_pct": round(ratio * 100, 0),
        }


def _empty_response(reason: str) -> dict:
    return {
        "summary": reason,
        "suggestions": [],
        "cache_hit": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _mock_response(reason: str) -> dict:
    return {"summary": reason, "suggestions": []}
