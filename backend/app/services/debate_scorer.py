"""辩论评分内核 —— 看多/看空 agent 对抗 + 判官裁决

阶段 2 评分(阶段 1 是 relevance_scorer 的单次 Haiku triage)。
仅当 should_escalate() 为真时由 debate_queue 异步调起。
全链路 fail-open:任何失败回退 triage,绝不丢信号。

spec: docs/superpowers/specs/2026-05-20-debate-scorer-design.md
"""

from __future__ import annotations

import logging

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
