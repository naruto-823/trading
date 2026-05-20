"""辩论异步执行 + 消费者

升级的快讯先落一条 push_status="debating" 的 event_notification 行(兼当去重锁),
再 submit_debate(row_id) 进有界线程池。消费者跑辩论 → 推 Bark → 更新同一行。
僵尸行(debating 超时)由 reconcile_stale_debates 用 triage 分强制收尾。

spec: docs/superpowers/specs/2026-05-20-debate-scorer-design.md §6-7、§9
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models.event_notification import EventNotification
from app.services.debate_scorer import build_position_context, run_debate
from app.services.notify import send_bark

logger = logging.getLogger(__name__)

_DIR_EMOJI = {"bullish": "📈", "bearish": "📉", "neutral": ""}
_DIR_LABEL = {"bullish": "看涨", "bearish": "看跌", "neutral": "中性"}

_executor: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=settings.debate_max_workers, thread_name_prefix="debate"
        )
    return _executor


def submit_debate(event_id: str) -> None:
    """把一条 debating 行的辩论任务丢进线程池(不阻塞调用方)。"""
    _get_executor().submit(_safe_process, event_id)


def _safe_process(event_id: str) -> None:
    try:
        process_escalated_event(event_id)
    except Exception:
        logger.exception("debate process crashed: %s", event_id)


def _triage_from_row(ev: EventNotification) -> dict:
    """从 debating 行重建 triage dict(行创建时已填入 triage 字段)。"""
    try:
        affected = json.loads(ev.affected_tickers_json) if ev.affected_tickers_json else []
    except (ValueError, TypeError):
        affected = []
    return {
        "relevance": ev.relevance or "indirect",
        "score": ev.relevance_score or 0,
        "sentiment": ev.sentiment or "neutral",
        "direction": ev.direction or "neutral",
        "confidence": ev.confidence or 50,
        "affected_tickers": affected,
        "reason": ev.relevance_reason or "",
        "model": settings.relevance_model,
    }


def format_debate_push(ev: EventNotification, verdict: dict) -> tuple[str, str, str]:
    """构造辩论结果的 Bark (title, body, level)。"""
    dir_emoji = _DIR_EMOJI.get(verdict["direction"], "")
    degraded = "[辩论降级]" if verdict.get("model") != "debate" else ""
    affected = verdict.get("affected_tickers") or []
    ticker_part = f"{affected[0]} " if affected else ""
    title = f"🧠{dir_emoji}{degraded} {ticker_part}{ev.title[:25]}"

    lines = [
        f"判官:{_DIR_LABEL.get(verdict['direction'], '中性')} · "
        f"综合{verdict['score']} · 可信度{verdict['confidence']}%"
    ]
    if verdict.get("bull_case"):
        lines.append(f"多: {verdict['bull_case'][:80]}")
    if verdict.get("bear_case"):
        lines.append(f"空: {verdict['bear_case'][:80]}")
    if verdict.get("judge_reasoning"):
        lines.append(f"判官: {verdict['judge_reasoning'][:120]}")
    lines.append(ev.title)
    body = "\n".join(lines)[:600]

    level = "timeSensitive" if ev.importance == "high" else "active"
    return title, body, level


def _apply_verdict(db: Session, ev: EventNotification, verdict: dict) -> None:
    """把 verdict 写回行,按阈值决定推不推。"""
    ev.relevance = verdict["relevance"]
    ev.relevance_score = verdict["score"]
    ev.relevance_reason = verdict["reason"]
    ev.sentiment = verdict["sentiment"]
    ev.direction = verdict["direction"]
    ev.confidence = verdict["confidence"]
    affected = verdict.get("affected_tickers") or []
    ev.affected_tickers_json = json.dumps(affected, ensure_ascii=False) if affected else None
    ev.symbol = affected[0] if affected else ev.symbol
    ev.debate_json = json.dumps({
        "bull_case": verdict.get("bull_case", ""),
        "bear_case": verdict.get("bear_case", ""),
        "judge_reasoning": verdict.get("judge_reasoning", ""),
        "winning_side": verdict.get("winning_side", "balanced"),
        "model": verdict.get("model", "debate"),
    }, ensure_ascii=False)

    if verdict["score"] < settings.relevance_threshold:
        ev.push_status = "skipped_low_relevance"
        db.commit()
        logger.info("debate skipped [score=%d]: %s", verdict["score"], ev.title[:60])
        return

    title, body, level = format_debate_push(ev, verdict)
    result = send_bark(title, body, level=level, group="market-events", sound="chime")
    ev.body = body
    ev.push_status = "sent" if result["ok"] else "failed"
    ev.push_error = None if result["ok"] else str(result["detail"])[:500]
    db.commit()
    logger.info("debate fired [score=%d dir=%s]: %s",
                verdict["score"], verdict["direction"], ev.title[:60])


def process_escalated_event(event_id: str) -> None:
    """消费一条 debating 行:跑辩论 → 推 Bark → 更新行。"""
    db = SessionLocal()
    try:
        ev = db.query(EventNotification).filter_by(id=event_id).first()
        if ev is None or ev.push_status != "debating":
            return
        triage = _triage_from_row(ev)
        position_ctx = build_position_context(triage["affected_tickers"])
        verdict = run_debate(ev.body, triage, position_ctx)
        _apply_verdict(db, ev, verdict)
    finally:
        db.close()


def reconcile_stale_debates(db: Session) -> int:
    """对账:debating 状态超过 debate_zombie_minutes 的僵尸行,用 triage 分强制收尾。

    返回收尾的行数。由 macro_flash worker 每轮调用。
    """
    cutoff = datetime.utcnow() - timedelta(minutes=settings.debate_zombie_minutes)
    stale = db.query(EventNotification).filter(
        EventNotification.push_status == "debating",
        EventNotification.notified_at < cutoff,
    ).all()
    for ev in stale:
        triage = _triage_from_row(ev)
        verdict = {
            "relevance": triage["relevance"],
            "score": triage["score"],
            "sentiment": triage["sentiment"],
            "direction": triage["direction"],
            "confidence": triage["confidence"],
            "affected_tickers": triage["affected_tickers"],
            "reason": f"[辩论超时降级] {triage['reason']}"[:200],
            "bull_case": "", "bear_case": "",
            "judge_reasoning": "辩论超时,回退 triage 快评",
            "winning_side": "balanced",
            "model": "debate-degraded",
        }
        _apply_verdict(db, ev, verdict)
    if stale:
        logger.warning("reconcile_stale_debates: 收尾 %d 条僵尸行", len(stale))
    return len(stale)
