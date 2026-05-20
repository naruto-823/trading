"""辩论评分内核 —— 看多/看空 agent 对抗 + 判官裁决

阶段 2 评分(阶段 1 是 relevance_scorer 的单次 Haiku triage)。
仅当 should_escalate() 为真时由 debate_queue 异步调起。
全链路 fail-open:任何失败回退 triage,绝不丢信号。

spec: docs/superpowers/specs/2026-05-20-debate-scorer-design.md
"""

from __future__ import annotations

import json
import logging

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from anthropic import Anthropic

from app.config import settings
from app.services.debate_research import gather_research

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


# —————————————————— 判官 + 编排 ——————————————————

JUDGE_SYSTEM_PROMPT = """你是中立『判官』。看多辩手和看空辩手各自给出了论证。
你的任务:权衡双方论据强弱、证据质量,给出最终裁决。
不要简单折中 —— 哪边论据更扎实就倒向哪边。
严格输出 JSON,不要 markdown 包裹:
{
  "relevance": "direct|indirect|noise",
  "score": <0-100 综合影响分,推送门槛用这个>,
  "sentiment": "positive|negative|neutral",
  "direction": "bullish|bearish|neutral",
  "confidence": <0-100 你对裁决的把握>,
  "affected_tickers": ["MSFT"],
  "reason": "<30字内结论>",
  "bull_case": "<采纳/转述的多方最强点>",
  "bear_case": "<采纳/转述的空方最强点>",
  "judge_reasoning": "<你为何这样裁:谁更有理、哪边证据弱>",
  "winning_side": "bull|bear|balanced"
}"""

_VALID_RELEVANCE = ("direct", "indirect", "noise")
_VALID_SENTIMENT = ("positive", "negative", "neutral")
_VALID_DIRECTION = ("bullish", "bearish", "neutral")


def _format_advocate(label: str, adv: dict | None) -> str:
    if adv is None:
        return f"{label}辩手:(缺席,本方陈词缺失)"
    points = ";".join(adv.get("key_points") or [])
    return (
        f"{label}辩手(立场强度 {adv.get('stance_score', 50)}):\n"
        f"  最强论证:{adv.get('strongest_argument', '')}\n"
        f"  论点:{points}\n"
        f"  自承软肋:{adv.get('risks_to_own_view', '')}"
    )


def _run_judge(
    content: str, position_ctx: str, brief: str,
    bull: dict | None, bear: dict | None,
) -> dict | None:
    """跑判官。失败返回 None。"""
    user = (
        f"{position_ctx}\n\n快讯:{content[:600]}\n\n"
        f"外部研究简报:{brief or '(无)'}\n\n"
        f"{_format_advocate('看多', bull)}\n\n{_format_advocate('看空', bear)}"
    )
    try:
        resp = _client(timeout=float(settings.debate_timeout_seconds)).messages.create(
            model=settings.debate_judge_model,
            max_tokens=700,
            system=JUDGE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        data = json.loads(_extract_json(resp))
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("debate judge failed: %s", exc)
        return None


def _verdict_from_triage(triage: dict, note: str) -> dict:
    """fail-open 回退:辩论挂了,用 triage 结果造一个 verdict。"""
    return {
        "relevance": triage.get("relevance", "indirect"),
        "score": int(triage.get("score", 0)),
        "sentiment": triage.get("sentiment", "neutral"),
        "direction": triage.get("direction", "neutral"),
        "confidence": int(triage.get("confidence", 50)),
        "affected_tickers": list(triage.get("affected_tickers") or []),
        "reason": f"[辩论降级] {note} · {triage.get('reason', '')}"[:200],
        "bull_case": "", "bear_case": "",
        "judge_reasoning": f"辩论未完成({note}),回退 triage 快评",
        "winning_side": "balanced",
        "model": "debate-degraded",
    }


def _normalize_verdict(judged: dict) -> dict:
    """把判官原始输出 clamp/规范成 DebateVerdict。"""
    relevance = judged.get("relevance")
    if relevance not in _VALID_RELEVANCE:
        relevance = "indirect"
    sentiment = judged.get("sentiment")
    if sentiment not in _VALID_SENTIMENT:
        sentiment = "neutral"
    direction = judged.get("direction")
    if direction not in _VALID_DIRECTION:
        direction = "neutral"
    winning = judged.get("winning_side")
    if winning not in ("bull", "bear", "balanced"):
        winning = "balanced"
    affected = judged.get("affected_tickers") or []
    if not isinstance(affected, list):
        affected = []
    affected = [str(t).upper().strip() for t in affected if t][:3]
    return {
        "relevance": relevance,
        "score": max(0, min(100, int(float(judged.get("score", 0) or 0)))),
        "sentiment": sentiment,
        "direction": direction,
        "confidence": max(0, min(100, int(float(judged.get("confidence", 50) or 50)))),
        "affected_tickers": affected,
        "reason": str(judged.get("reason", ""))[:200],
        "bull_case": str(judged.get("bull_case", ""))[:400],
        "bear_case": str(judged.get("bear_case", ""))[:400],
        "judge_reasoning": str(judged.get("judge_reasoning", ""))[:600],
        "winning_side": winning,
        "model": "debate",
    }


def run_debate(content: str, triage: dict, position_ctx: str) -> dict:
    """跑完整辩论。永远返回一个 verdict dict(失败 fail-open 回退 triage)。

    verdict 是现有 scorer 字段(relevance/score/sentiment/direction/confidence/
    affected_tickers/reason)的超集,额外含 bull_case/bear_case/judge_reasoning/
    winning_side/model。
    """
    tickers = list(triage.get("affected_tickers") or [])
    brief = gather_research(content, tickers)

    bull: dict | None = None
    bear: dict | None = None
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="advocate") as ex:
            f_bull = ex.submit(
                _run_advocate, "bull", settings.debate_bull_model,
                content, position_ctx, brief,
            )
            f_bear = ex.submit(
                _run_advocate, "bear", settings.debate_bear_model,
                content, position_ctx, brief,
            )
            timeout = float(settings.debate_timeout_seconds)
            bull = f_bull.result(timeout=timeout)
            bear = f_bear.result(timeout=timeout)
    except FutureTimeout:
        logger.warning("debate advocates timed out")
    except Exception as exc:
        logger.warning("debate advocates error: %s", exc)

    if bull is None and bear is None:
        return _verdict_from_triage(triage, "多空均失败")

    judged = _run_judge(content, position_ctx, brief, bull, bear)
    if judged is None:
        return _verdict_from_triage(triage, "判官失败")

    try:
        return _normalize_verdict(judged)
    except Exception as exc:
        logger.warning("debate verdict normalize failed: %s", exc)
        return _verdict_from_triage(triage, "结果归一化失败")
