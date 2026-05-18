"""每日财经日历早报

每天北京时间 8:00（UTC 00:00）跑：
- 拉本周金十财经日历
- 筛选今日 + 明日 + star >= 3 的重要事件
- 拼成一条 Bark 推送，让用户知道今天/明天有哪些 market-moving 事件

避免每天反复推同一个日历：用 event_notification 表按 "calendar-YYYYMMDD" 做日级去重。
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi.concurrency import run_in_threadpool

from app.db import SessionLocal
from app.models.event_notification import EventNotification
from app.services.mcp_jin10 import is_configured as mcp_ready, list_calendar
from app.services.notify import send_bark
from app.workers.scheduler import record_duration

logger = logging.getLogger(__name__)

JOB_ID = "calendar-briefing"

# 只关心 ★★★ 及以上（市场关键数据 / 央行决议 / 重要讲话）
MIN_STAR = 3

# 推送当天 + 明天的事件
LOOKAHEAD_DAYS = 2

# 用 UTC+8 算"今天"，跟金十 pub_time 时区一致
HK_TZ = timezone(timedelta(hours=8))


def _format_event(entry: dict) -> str:
    """单条事件文案：'14:00 ★★★ 美国 4月CPI（预期 +2.4%）'"""
    pub = entry.get("pub_time", "")
    # pub_time 是 "2026-05-21 14:30" 这种，取 HH:MM
    time_str = pub.split(" ", 1)[1][:5] if " " in pub else pub
    stars = "★" * entry.get("star", 0)
    title = entry.get("title", "")
    consensus = entry.get("consensus")
    line = f"{time_str} {stars} {title}"
    if consensus:
        line += f"（预期 {consensus}）"
    return line


def _build_message(entries: list[dict]) -> tuple[str, str]:
    """按日期分组，构造 (title, body)"""
    now_hk = datetime.now(HK_TZ)
    today = now_hk.date()
    tomorrow = today + timedelta(days=1)

    today_items: list[dict] = []
    tomorrow_items: list[dict] = []
    for e in entries:
        pub = e.get("pub_time", "")
        try:
            event_date = datetime.strptime(pub.split(" ", 1)[0], "%Y-%m-%d").date()
        except Exception:
            continue
        if event_date == today:
            today_items.append(e)
        elif event_date == tomorrow:
            tomorrow_items.append(e)

    title = f"📅 财经日历 · {today.strftime('%m/%d')} ({len(today_items)} 重大事件)"

    body_parts: list[str] = []
    if today_items:
        body_parts.append("【今日】")
        body_parts.extend(_format_event(e) for e in today_items[:8])
    else:
        body_parts.append("【今日】无 ★★★ 级别事件")

    if tomorrow_items:
        body_parts.append("")
        body_parts.append("【明日】")
        body_parts.extend(_format_event(e) for e in tomorrow_items[:6])

    body = "\n".join(body_parts)
    if len(body) > 1500:
        body = body[:1500] + "…"
    return title, body


def _daily_hash() -> str:
    """每天一个 hash，避免同一天反复推送"""
    today_str = datetime.now(HK_TZ).strftime("%Y%m%d")
    return hashlib.sha256(f"calendar|{today_str}".encode()).hexdigest()[:32]


def _run_once_sync() -> dict[str, int]:
    stats = {"total": 0, "important": 0, "pushed": 0, "skipped": 0}
    if not mcp_ready():
        stats["skipped"] = 1
        return stats

    entries = list_calendar()
    stats["total"] = len(entries)
    important = [e for e in entries if e.get("star", 0) >= MIN_STAR]
    stats["important"] = len(important)

    if not important:
        return stats

    db = SessionLocal()
    try:
        h = _daily_hash()
        if db.query(EventNotification).filter_by(event_hash=h).first():
            stats["skipped"] = 1  # 今天已经推过
            return stats

        title, body = _build_message(important)
        result = send_bark(title, body, level="active", group="market-events", sound="chime")

        rec = EventNotification(
            id=uuid.uuid4().hex,
            event_hash=h,
            notified_at=datetime.utcnow(),
            symbol=None,
            importance="medium",
            title=title,
            body=body,
            source_title=f"calendar-{datetime.now(HK_TZ).strftime('%Y%m%d')}",
            push_status="sent" if result["ok"] else "failed",
            push_error=None if result["ok"] else str(result["detail"])[:500],
        )
        db.add(rec)
        db.commit()

        if result["ok"]:
            stats["pushed"] = 1
            logger.info("calendar-briefing pushed: %d events today + %d tomorrow",
                        sum(1 for e in important if e.get("pub_time", "").startswith(datetime.now(HK_TZ).strftime("%Y-%m-%d"))),
                        sum(1 for e in important if e.get("pub_time", "").startswith((datetime.now(HK_TZ) + timedelta(days=1)).strftime("%Y-%m-%d"))))
    finally:
        db.close()
    return stats


async def run_calendar_briefing() -> None:
    t0 = time.time()
    try:
        stats = await run_in_threadpool(_run_once_sync)
        if stats["pushed"] or stats["important"]:
            logger.info(
                "calendar-briefing: total=%d important=%d pushed=%d skipped=%d",
                stats["total"], stats["important"], stats["pushed"], stats["skipped"],
            )
    finally:
        record_duration(JOB_ID, int((time.time() - t0) * 1000))


def register(sched: AsyncIOScheduler) -> None:
    """每天 UTC 00:00 = 北京 08:00 跑一次"""
    sched.add_job(
        run_calendar_briefing,
        trigger=CronTrigger(hour=0, minute=0, timezone="UTC"),
        id=JOB_ID,
        name="财经日历早报（每天 08:00 北京时间，今日+明日 ★★★ 事件）",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,  # 错过 1h 内还跑
    )
