"""事件监控 worker

每 30 min 跑一次：detect_events → process_events（推 Bark + 入库 + 去重）
LLM 调用约 $0.05/次，30min 一次 = $2-3/day。
"""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi.concurrency import run_in_threadpool

from app.db import SessionLocal
from app.services.event_watcher import detect_events, process_events
from app.workers.scheduler import record_duration

logger = logging.getLogger(__name__)

JOB_ID = "event-watcher"


def _run_once_sync() -> dict[str, int]:
    db = SessionLocal()
    try:
        events = detect_events(db)
        if not events:
            return {"detected": 0, "fired": 0, "deduped": 0, "failed": 0}
        return process_events(db, events)
    finally:
        db.close()


async def run_event_watcher() -> None:
    t0 = time.time()
    try:
        stats = await run_in_threadpool(_run_once_sync)
        if stats["detected"]:
            logger.info(
                "event-watcher: detected=%d fired=%d deduped=%d failed=%d",
                stats["detected"], stats["fired"], stats["deduped"], stats["failed"],
            )
    finally:
        record_duration(JOB_ID, int((time.time() - t0) * 1000))


def register(sched: AsyncIOScheduler) -> None:
    """30 min interval。频次再高反而更多 LLM 噪声 + 成本上升。"""
    sched.add_job(
        run_event_watcher,
        trigger=IntervalTrigger(minutes=30, timezone="UTC"),
        id=JOB_ID,
        name="市场事件监控（30min，LLM 识别重大新闻）",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
