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


_URGENCY_DOWNGRADE = {"high": "medium", "medium": "low", "low": "low"}


def downgrade_urgency(urgency: str) -> str:
    """urgency 降一档(high→medium→low,low 保持)。未知值兜底为 low。"""
    return _URGENCY_DOWNGRADE.get(urgency, "low")


_DIR_CN = {"bullish": "涨", "bearish": "跌", "neutral": "平"}


def debate_annotation(consistency: str, action: str, verdict: dict) -> str:
    """生成追加到 thesis 末尾的辩论复核行。"""
    conf = verdict.get("confidence", 0)
    judge = (verdict.get("judge_reasoning") or "")[:120]
    if consistency == "agree":
        side = verdict.get("winning_side", "")
        return f"⚖️ 辩论复核:判官同向({side},判官 {conf}%)— {judge}"
    if consistency == "contradict":
        dir_cn = _DIR_CN.get(verdict.get("direction"), "平")
        # 引与建议动作相反那一方的 case:卖建议被判看涨→bull_case;买建议被判看跌→bear_case
        if action in _BEARISH_ACTIONS:
            opp_case = verdict.get("bull_case") or ""
        else:
            opp_case = verdict.get("bear_case") or ""
        return (
            f"⚖️ 辩论复核:判官倾向看{dir_cn},与本动作相左 —— "
            f"{opp_case[:120]}。两可,你来定。"
        )
    # mixed
    return f"⚖️ 辩论复核:多空僵持/存疑 — {judge}"


def apply_debate(suggestion: dict, verdict: dict) -> None:
    """把一个 verdict 应用到一条建议(in-place):
    分类一致性 → contradict/mixed 时降 urgency → 追加 thesis 行 → 写 debate 字段。
    """
    action = suggestion.get("action", "")
    consistency = classify_consistency(action, verdict)
    if consistency in ("contradict", "mixed"):
        suggestion["urgency"] = downgrade_urgency(suggestion.get("urgency", "medium"))
    annotation = debate_annotation(consistency, action, verdict)
    base_thesis = suggestion.get("thesis", "")
    suggestion["thesis"] = f"{base_thesis}\n{annotation}" if base_thesis else annotation
    suggestion["debate"] = {
        "direction": verdict.get("direction", "neutral"),
        "winning_side": verdict.get("winning_side", "balanced"),
        "confidence": verdict.get("confidence", 0),
        "consistency": consistency,
        "bull_case": verdict.get("bull_case", ""),
        "bear_case": verdict.get("bear_case", ""),
        "judge_reasoning": verdict.get("judge_reasoning", ""),
    }
