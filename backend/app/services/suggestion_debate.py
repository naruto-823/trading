"""建议引擎的辩论复核(Phase 2)

对 Opus 批量产出的建议做看多/看空辩论二次复核 —— 跑 Phase 1 的 run_debate 拿独立
第二意见,verdict 与建议动作矛盾时标注 + 降 urgency(不删不改动作)。
in-place 后处理,跟 suggestions._verify_prices / _check_affordability 同模式。

spec: docs/superpowers/specs/2026-05-20-debate-scorer-phase2-design.md
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from app.config import settings
from app.services.debate_scorer import build_position_context, run_debate

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


def _is_option(symbol: str) -> bool:
    """期权合约 symbol 识别(如 MSFT260618C440000.US)。

    本地副本 —— 与 suggestions._is_option 同口径,独立定义以避免 suggestions ↔
    suggestion_debate 的循环 import。
    """
    return (
        len(symbol) > 12
        and any(c in symbol for c in ("C", "P"))
        and any(d.isdigit() for d in symbol)
    )


def _synth_inputs(symbol: str) -> tuple[str, dict]:
    """合成喂给 run_debate 的中性 content + triage(不喂建议动作,要独立第二意见)。"""
    content = (
        f"持仓复核请求:对用户持仓标的 {symbol} 做一次独立的多空方向评估 —— "
        f"结合该标的近况与下方持仓信息,判断此刻应看多还是看空 {symbol}。"
    )
    triage = {
        "relevance": "direct", "score": 60, "sentiment": "neutral",
        "direction": "neutral", "confidence": 50,
        "affected_tickers": [symbol], "reason": "建议复核", "model": "suggestion",
    }
    return content, triage


def _debate_one(symbol: str) -> dict | None:
    """跑一个 symbol 的辩论复核。失败返回 None。"""
    try:
        content, triage = _synth_inputs(symbol)
        position_ctx = build_position_context([symbol])
        return run_debate(content, triage, position_ctx)
    except Exception as exc:
        logger.warning("suggestion debate failed for %s: %s", symbol, exc)
        return None


def debate_batch(suggestions: list[dict]) -> None:
    """对建议批次做辩论复核 —— in-place 改每条建议的 urgency/thesis/debate。

    debate_enabled=False → no-op。期权合约 symbol 跳过。同 symbol 只辩一次。
    单 symbol 失败被隔离,不影响其他建议。
    """
    if not settings.debate_enabled:
        return
    symbols = {
        s["symbol"]
        for s in suggestions
        if s.get("symbol") and not _is_option(s["symbol"])
    }
    if not symbols:
        return
    verdicts: dict[str, dict | None] = {}
    with ThreadPoolExecutor(
        max_workers=max(1, settings.debate_max_workers), thread_name_prefix="sug-debate"
    ) as ex:
        futures = {sym: ex.submit(_debate_one, sym) for sym in symbols}
        for sym, fut in futures.items():
            try:
                verdicts[sym] = fut.result()
            except Exception as exc:
                logger.warning("suggestion debate future failed for %s: %s", sym, exc)
                verdicts[sym] = None
    for s in suggestions:
        verdict = verdicts.get(s.get("symbol", ""))
        if verdict is not None:
            apply_debate(s, verdict)
