"""共享新闻处理管道:filter → 去重 → 评分 → 门控 → 推送。

Jin10 实时(`jin10_browser_worker`)与 Discord 个股新闻(`discord_news_worker`)
共用这一套,避免逻辑复制。原先内联在 jin10 worker 里的 `_process_flash_sync`
抽到这里参数化。

每条新闻经过:
  matches_keywords 过滤 → event_notification 去重(可带跨源 hash)
  → score_relevance 评分 → should_escalate? 升级看多看空辩论
  → 低分落库跳过 / 高分推 Bark
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

from app.config import settings
from app.db import SessionLocal
from app.models.event_notification import EventNotification
from app.services.debate_queue import submit_debate
from app.services.debate_scorer import should_escalate
from app.services.macro_feed import MacroFlash
from app.services.macro_pusher import matches_keywords
from app.services.notify import send_bark
from app.services.relevance_scorer import score_relevance

logger = logging.getLogger(__name__)

_DIR_EMOJI = {"bullish": "📈", "bearish": "📉", "neutral": ""}


def _bump(state: dict | None, key: str) -> None:
    if state is not None:
        state[key] = state.get(key, 0) + 1


def process_news_item(
    content: str,
    *,
    source: str,
    dedup_seed: str,
    is_important: bool = False,
    known_tickers: Sequence[str] | None = None,
    cross_source_hashes: Sequence[str] = (),
    source_label: str = "",
    tags: Sequence[str] | None = None,
    state: dict | None = None,
) -> str:
    """处理单条新闻,返回结果状态。

    参数:
        content: 新闻正文
        source: 去重命名空间 + 来源标记(如 "jin10-rt" / "discord")
        dedup_seed: 去重种子(jin10 用 flash id / Discord 用 ticker+正文)
        is_important: 是否高优先(影响推送级别与 importance)
        known_tickers: 已知受影响标的(Discord 拆条直接带来,优先于评分反推)
        cross_source_hashes: 跨源去重 hash(命中任一即视为重复)
        source_label: 推送标题里的来源名(如 "金十" / "Discord")
        tags: MacroFlash 标签(用于关键词过滤)
        state: 可选统计计数器

    返回: "empty"|"filtered"|"deduped"|"escalated"|"skipped_low"|"sent"|"failed"
    """
    content = (content or "").strip()
    if not content:
        return "empty"

    item = MacroFlash(
        time=datetime.now(timezone.utc),
        title=content[:80],
        content=content,
        importance=5 if is_important else 3,
        source=source,
        tags=list(tags) if tags else [],
    )

    if not matches_keywords(item):
        _bump(state, "filtered")
        return "filtered"

    h = hashlib.sha256(f"{source}|{dedup_seed}".encode()).hexdigest()[:32]
    all_hashes = [h, *cross_source_hashes]

    db = SessionLocal()
    try:
        if (
            db.query(EventNotification)
            .filter(EventNotification.event_hash.in_(all_hashes))
            .first()
        ):
            _bump(state, "deduped")
            return "deduped"

        scoring = score_relevance(content)
        score = scoring["score"]
        affected = list(known_tickers) if known_tickers else scoring["affected_tickers"]
        affected_json = json.dumps(affected, ensure_ascii=False) if affected else None
        symbol = affected[0] if affected else None

        common_kwargs = dict(
            id=uuid.uuid4().hex,
            event_hash=h,
            notified_at=datetime.utcnow(),
            symbol=symbol,
            source_title=f"[{source}] {content[:200]}",
            relevance=scoring["relevance"],
            relevance_score=score,
            relevance_reason=scoring["reason"],
            sentiment=scoring["sentiment"],
            direction=scoring["direction"],
            confidence=scoring["confidence"],
            affected_tickers_json=affected_json,
        )
        importance = "high" if is_important else "medium"

        # 高 stakes:升级到完整辩论(异步,不在此处推送)
        if should_escalate(scoring, item.importance):
            rec = EventNotification(
                **common_kwargs,
                importance=importance,
                title=content[:200],
                body=content[:500],
                push_status="debating",
                push_error=None,
            )
            db.add(rec)
            db.commit()
            submit_debate(rec.id)
            _bump(state, "escalated")
            logger.info("%s escalated to debate: %s", source, content[:60])
            return "escalated"

        if score < settings.relevance_threshold:
            rec = EventNotification(
                **common_kwargs,
                importance=importance,
                title=content[:200],
                body=content[:500],
                push_status="skipped_low_relevance",
                push_error=None,
            )
            db.add(rec)
            db.commit()
            _bump(state, "scored_low")
            logger.info(
                "%s skipped [score=%d %s dir=%s]: %s",
                source, score, scoring["relevance"], scoring["direction"], content[:60],
            )
            return "skipped_low"

        # 高分:推 Bark
        marker = "⚡" if is_important else "📡"
        dir_icon = _DIR_EMOJI.get(scoring["direction"], "")
        ticker_part = f"{affected[0]} " if affected else ""
        label = f"[{source_label}] " if source_label else ""
        title = f"{marker}{dir_icon}{label}{ticker_part}{content[:25]}{'…' if len(content) > 25 else ''}"

        body_lines = []
        if scoring["sentiment"] != "neutral" or scoring["direction"] != "neutral":
            sent = {"positive": "利好", "negative": "利空", "neutral": "中性"}[scoring["sentiment"]]
            dir_label = {"bullish": "看涨", "bearish": "看跌", "neutral": "中性"}[scoring["direction"]]
            body_lines.append(f"{sent} · {dir_label} · 可信度 {scoring['confidence']}%")
        body_lines.append(content)
        body = "\n".join(body_lines)[:500]

        level = "timeSensitive" if is_important else "active"
        result = send_bark(title, body, level=level, group="market-events", sound="chime")

        rec = EventNotification(
            **common_kwargs,
            importance=importance,
            title=content[:200],
            body=body,
            push_status="sent" if result["ok"] else "failed",
            push_error=None if result["ok"] else str(result["detail"])[:500],
        )
        db.add(rec)
        db.commit()

        if result["ok"]:
            _bump(state, "fired")
            logger.info("%s fired [score=%d dir=%s]: %s", source, score, scoring["direction"], content[:80])
            return "sent"
        return "failed"
    finally:
        db.close()
