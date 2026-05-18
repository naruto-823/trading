"""Quick Assess 相关性评分 —— 推送前的门控

作用：关键词命中 ≠ 真有传导。用 Haiku 4.5 用户持仓做 context 快速判断
快讯对用户的实际影响程度（direct / indirect / noise + 0-100 分）。
score >= settings.relevance_threshold 才真推 Bark，否则只落库（dashboard 可看）。

成本：Haiku 4.5 单次 ~$0.001，每天 200-500 次评分 ≈ $0.5/天。
延迟：< 1s（Haiku 比 Opus 快 5-10 倍）。
Fail-open：scorer 任何失败都返回 score=100（避免 LLM 挂掉漏 signal）。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from anthropic import Anthropic

from app.config import settings
from app.db import SessionLocal
from app.services.positions import list_positions

logger = logging.getLogger(__name__)

# positions context 5 分钟缓存
_positions_cache: tuple[str, float] | None = None
_POSITIONS_CACHE_TTL = 300


SYSTEM_PROMPT = """你是金融快讯的相关性评分员。给一条快讯 + 用户的持仓清单，判断这条新闻对用户**实际影响程度**。

输出严格 JSON（不要 markdown 包裹）：
{"relevance": "direct|indirect|noise", "score": <0-100整数>, "reason": "一句话30字内"}

【评分标准】
- direct (70-100)：明确点名用户持仓的 ticker / 公司名；或全球宏观重大事件（FOMC决议、CPI/PPI/非农数据、关键地缘升级）
- indirect (40-70)：同行业 / 供应链 / 竞品 / 相关监管，需 1-2 步推理才能传导到持仓
- noise (0-40)：A 股个股动态 / 跟持仓行业完全无关 / 国内地方性新闻 / 普通行业政策

【硬规则】
- 用户持仓基本是美股 mega cap tech（MSFT/META/QQQ/TSLA/GOOG/AAPL/TSM）+ 一两只港股
- **A 股个股新闻一律 noise**（即使含"芯片"/"AI"等关键词），除非该 A 股本身就是持仓
- **国内地方政府 / 小公司新闻一律 noise**
- 美联储 / CPI / PPI / 非农 / 地缘升级 一律 direct
- reason 必须解释为什么是这个分数

只输出 JSON，不要任何 markdown 包裹。"""


def _get_positions_context() -> str:
    """5 min 缓存的持仓简介，给 scorer prompt 用"""
    global _positions_cache
    now = time.time()
    if _positions_cache and (now - _positions_cache[1]) < _POSITIONS_CACHE_TTL:
        return _positions_cache[0]

    db = SessionLocal()
    try:
        positions = list_positions(db)
        # 排除期权 + 取前 8 大持仓
        stocks = sorted(
            [p for p in positions if len(p.symbol) <= 8 and abs(p.market_value) > 0],
            key=lambda p: abs(p.market_value),
            reverse=True,
        )[:8]
        if not stocks:
            ctx = "用户当前无持仓"
        else:
            parts = [f"{p.symbol}({p.name})" for p in stocks]
            ctx = "用户重仓 (按市值倒序): " + ", ".join(parts)
        _positions_cache = (ctx, now)
        return ctx
    finally:
        db.close()


def invalidate_positions_cache() -> None:
    """同步后调用，让下次评分用最新持仓"""
    global _positions_cache
    _positions_cache = None


def score_relevance(content: str) -> dict[str, Any]:
    """评分一条快讯。永远返回字典，永不抛异常（fail-open）。

    返回：
        {"relevance": "direct|indirect|noise", "score": int, "reason": str, "model": str}
        fail-open 时 score=100，让它通过推送
    """
    if not content or len(content.strip()) < 5:
        return {"relevance": "noise", "score": 0, "reason": "内容太短", "model": "rule"}

    # 阈值=0 表示禁用 scorer，全推（保留 score=100 让后续逻辑通过）
    if settings.relevance_threshold <= 0:
        return {"relevance": "direct", "score": 100, "reason": "scorer 已禁用", "model": "none"}

    if not settings.anthropic_api_key:
        return {"relevance": "direct", "score": 100, "reason": "AI 未配置 → fail-open", "model": "none"}

    positions_ctx = _get_positions_context()
    user_prompt = f"{positions_ctx}\n\n快讯内容：{content[:600]}"

    try:
        client = Anthropic(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url or None,
            timeout=5.0,  # 卡死 5s，超时就 fail-open
        )
        resp = client.messages.create(
            model=settings.relevance_model,
            max_tokens=200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        # 去掉可能的 markdown 包裹
        if text.startswith("```"):
            first_nl = text.find("\n")
            if first_nl > 0:
                text = text[first_nl + 1:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        data = json.loads(text)
        score = int(data.get("score", 0))
        score = max(0, min(100, score))
        relevance = data.get("relevance", "noise")
        if relevance not in ("direct", "indirect", "noise"):
            relevance = "noise"
        return {
            "relevance": relevance,
            "score": score,
            "reason": str(data.get("reason", ""))[:200],
            "model": settings.relevance_model,
        }
    except Exception as exc:
        logger.warning("relevance_scorer fail-open: %s", exc)
        return {
            "relevance": "direct",
            "score": 100,
            "reason": f"scorer 异常 fail-open: {type(exc).__name__}",
            "model": "fail-open",
        }
