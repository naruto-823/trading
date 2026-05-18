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


SYSTEM_PROMPT = """你是金融快讯多维度评分员。给一条快讯 + 用户持仓清单，输出严格 JSON：

{
  "relevance": "direct|indirect|noise",
  "score": <0-100整数>,
  "sentiment": "positive|negative|neutral",
  "direction": "bullish|bearish|neutral",
  "confidence": <0-100整数>,
  "affected_tickers": ["MSFT", "TSLA"],
  "reason": "30字内"
}

【relevance 相关性】
- direct：明确点名用户持仓 ticker / 公司名；或全球宏观重大事件（FOMC / CPI / PPI / 非农 / 地缘大升级）
- indirect：同行业 / 供应链 / 竞品 / 相关监管，需 1-2 步推理传导
- noise：A 股个股 / 跟持仓行业完全无关 / 国内地方性 / 小公司动态

【sentiment 利好利空】（新闻本身的性质）
- positive：订单 / 收购 / 业绩超预期 / 监管利好 / 大客户拿下
- negative：监管 / 诉讼 / 召回 / 业绩 miss / 高管离任 / 事故
- neutral：人事变动 / 数据公布 / 中性披露

【direction 看涨看跌】（对用户持仓的方向影响 —— 该加仓还是减仓？）
- bullish：看涨，可加仓 / 持有
- bearish：看跌，应警惕 / 减仓
- neutral：影响中性 / 已 priced in / 方向不明

注意 sentiment 和 direction 不一定相同：
- "美联储意外加息"：sentiment=neutral（数据），direction=bearish（对科技股）
- "苹果发布新 iPad"：sentiment=positive，direction=neutral（已 priced in）

【confidence 可信度】你对自己评分的把握度
- 80-100：来自权威源 + 明确事件 + 已发生
- 50-80：明确事件但传导路径有推理
- 20-50：传闻 / 猜测 / 间接 / 数据不全
- < 20：极度不确定

【score 综合分】= 你自己根据上面四维度综合给的 0-100：
- direct + 高 confidence + 明确 direction = 80-100（推荐用 timeSensitive 推）
- direct + 中 confidence = 60-80
- indirect + 高 confidence + 强 direction = 50-70
- noise / 很低 confidence = 0-40
- 跟用户重仓直接相关的（affected_tickers 非空）score 至少 +15

【affected_tickers】受影响的用户**持仓** ticker（最多 3 个；无受影响则 []）

【硬规则】
- 用户重仓基本是美股 mega cap tech + 港股
- A 股个股新闻 affected_tickers 必为 []，relevance=noise，score < 30（即使含"芯片"/"AI"）
- 美联储 / CPI / 油价突变 / 地缘升级：relevance=direct，score >= 70

只输出 JSON 不要 markdown 包裹。"""


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


def _fail_open(reason: str) -> dict[str, Any]:
    """异常情况返回 score=100 的兜底字典，让快讯通过推送（避免漏 signal）"""
    return {
        "relevance": "direct", "score": 100,
        "sentiment": "neutral", "direction": "neutral", "confidence": 50,
        "affected_tickers": [],
        "reason": reason, "model": "fail-open",
    }


def score_relevance(content: str) -> dict[str, Any]:
    """评分一条快讯。永远返回字典，永不抛异常（fail-open）。

    返回结构：
        {
            "relevance": "direct|indirect|noise",
            "score": int (0-100),
            "sentiment": "positive|negative|neutral",
            "direction": "bullish|bearish|neutral",
            "confidence": int (0-100),
            "affected_tickers": list[str],
            "reason": str,
            "model": str,
        }
    """
    if not content or len(content.strip()) < 5:
        return {**_fail_open("内容太短"), "score": 0, "relevance": "noise", "model": "rule"}

    if settings.relevance_threshold <= 0:
        return _fail_open("scorer 已禁用")

    if not settings.anthropic_api_key:
        return _fail_open("AI 未配置")

    positions_ctx = _get_positions_context()
    user_prompt = f"{positions_ctx}\n\n快讯内容：{content[:600]}"

    try:
        client = Anthropic(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url or None,
            timeout=8.0,  # 多维度判断稍慢一点
        )
        resp = client.messages.create(
            model=settings.relevance_model,
            max_tokens=400,  # 输出更多字段
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        if text.startswith("```"):
            first_nl = text.find("\n")
            if first_nl > 0:
                text = text[first_nl + 1:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        data = json.loads(text)

        # 规范化 + clamp
        relevance = data.get("relevance", "noise")
        if relevance not in ("direct", "indirect", "noise"):
            relevance = "noise"
        sentiment = data.get("sentiment", "neutral")
        if sentiment not in ("positive", "negative", "neutral"):
            sentiment = "neutral"
        direction = data.get("direction", "neutral")
        if direction not in ("bullish", "bearish", "neutral"):
            direction = "neutral"

        score = max(0, min(100, int(data.get("score", 0))))
        confidence = max(0, min(100, int(data.get("confidence", 50))))

        affected = data.get("affected_tickers") or []
        if not isinstance(affected, list):
            affected = []
        affected = [str(t).upper().strip() for t in affected if t][:3]

        return {
            "relevance": relevance,
            "score": score,
            "sentiment": sentiment,
            "direction": direction,
            "confidence": confidence,
            "affected_tickers": affected,
            "reason": str(data.get("reason", ""))[:200],
            "model": settings.relevance_model,
        }
    except Exception as exc:
        logger.warning("relevance_scorer fail-open: %s", exc)
        return _fail_open(f"scorer 异常: {type(exc).__name__}")
