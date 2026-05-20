"""建议引擎的辩论复核(Phase 2)

对 Opus 批量产出的建议做看多/看空辩论二次复核 —— 跑 Phase 1 的 run_debate 拿独立
第二意见,verdict 与建议动作矛盾时标注 + 降 urgency(不删不改动作)。
in-place 后处理,跟 suggestions._verify_prices / _check_affordability 同模式。

spec: docs/superpowers/specs/2026-05-20-debate-scorer-phase2-design.md
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 动作 → 隐含方向
_BULLISH_ACTIONS = ("buy", "add")
_BEARISH_ACTIONS = ("sell", "stop_loss")


def classify_consistency(action: str, verdict: dict) -> str:
    """建议动作 vs 辩论 verdict → "agree" / "contradict" / "mixed"。"""
    if verdict.get("model") == "debate-degraded":
        return "mixed"
    if verdict.get("winning_side") == "balanced":
        return "mixed"
    direction = verdict.get("direction")
    if direction not in ("bullish", "bearish"):
        return "mixed"
    if action in _BULLISH_ACTIONS:
        implied = "bullish"
    elif action in _BEARISH_ACTIONS:
        implied = "bearish"
    else:
        return "mixed"  # 未知动作
    return "agree" if direction == implied else "contradict"
