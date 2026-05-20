"""辩论评分内核 —— 看多/看空 agent 对抗 + 判官裁决

阶段 2 评分(阶段 1 是 relevance_scorer 的单次 Haiku triage)。
仅当 should_escalate() 为真时由 debate_queue 异步调起。
全链路 fail-open:任何失败回退 triage,绝不丢信号。

spec: docs/superpowers/specs/2026-05-20-debate-scorer-design.md
"""

from __future__ import annotations

import json
import logging

from anthropic import Anthropic

from app.config import settings

logger = logging.getLogger(__name__)


def should_escalate(triage: dict, source_importance: int) -> bool:
    """两阶段门控:triage 结果 → 要不要升级到完整辩论。

    triage: relevance_scorer.score_relevance() 的返回 dict
    source_importance: 快讯源标的重要度(MacroFlash.importance,int)
    """
    if not settings.debate_enabled:
        return False
    # triage 自己挂了(fail-open),别再升级 —— 让它走快路兜底推送
    if triage.get("model") == "fail-open":
        return False
    if triage.get("affected_tickers"):
        return True
    if source_importance >= settings.debate_escalate_min_importance:
        return True
    score = int(triage.get("score", 0))
    return settings.debate_escalate_score_lo <= score <= settings.debate_escalate_score_hi


# —————————————————— 辩手(看多 / 看空)——————————————————

BULL_SYSTEM_PROMPT = """你是『看多辩手』。给一条金融快讯 + 用户持仓 + 研究简报,
你的任务:为「这对用户持仓是利好 / 应看涨」构建最强论证 —— 即使你内心不完全认同,
也要找出最有力的多方理由。严格输出 JSON,不要 markdown 包裹:
{
  "stance_score": <0-100,你为多方立场打的强度分>,
  "key_points": ["论点1", "论点2", ...最多4条],
  "strongest_argument": "最强的一条多方论证(<300字)",
  "risks_to_own_view": "诚实指出多方立场最大的软肋(<200字)"
}"""

BEAR_SYSTEM_PROMPT = """你是『看空辩手』。给一条金融快讯 + 用户持仓 + 研究简报,
你的任务:为「这对用户持仓是利空 / 应看跌」构建最强论证 —— 即使你内心不完全认同,
也要找出最有力的空方理由。严格输出 JSON,不要 markdown 包裹:
{
  "stance_score": <0-100,你为空方立场打的强度分>,
  "key_points": ["论点1", "论点2", ...最多4条],
  "strongest_argument": "最强的一条空方论证(<300字)",
  "risks_to_own_view": "诚实指出空方立场最大的软肋(<200字)"
}"""


def _client(timeout: float) -> Anthropic:
    return Anthropic(
        api_key=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
        timeout=timeout,
    )


def _extract_json(resp) -> str:
    """从 messages 响应里取文本并剥掉 ``` 围栏。"""
    text = "".join(
        b.text for b in resp.content if getattr(b, "type", "") == "text"
    ).strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl > 0:
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return text


def _run_advocate(
    side: str, model: str, content: str, position_ctx: str, brief: str
) -> dict | None:
    """跑一个辩手(side='bull' 或 'bear')。失败返回 None。"""
    system = BULL_SYSTEM_PROMPT if side == "bull" else BEAR_SYSTEM_PROMPT
    user = (
        f"{position_ctx}\n\n快讯:{content[:600]}\n\n"
        f"外部研究简报:{brief or '(无外部数据,仅凭快讯判断)'}"
    )
    try:
        resp = _client(timeout=float(settings.debate_timeout_seconds)).messages.create(
            model=model,
            max_tokens=600,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        data = json.loads(_extract_json(resp))
        return {
            "side": side,
            "stance_score": max(0, min(100, int(data.get("stance_score", 50)))),
            "key_points": [str(p)[:120] for p in (data.get("key_points") or [])][:4],
            "strongest_argument": str(data.get("strongest_argument", ""))[:300],
            "risks_to_own_view": str(data.get("risks_to_own_view", ""))[:200],
        }
    except Exception as exc:
        logger.warning("debate advocate %s failed: %s", side, exc)
        return None
