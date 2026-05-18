"""快通道宏观推送 worker：3 min 跑一次

不调 LLM、不耗 token —— 纯规则（源标重要 + 关键词白名单）。
覆盖 event-watcher 30 min 间隔 + LLM 判断的时效延迟。
去重靠 event_notification 共用表，跟 event-watcher 不会重复推送。
"""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi.concurrency import run_in_threadpool

from app.db import SessionLocal
from app.services.macro_pusher import run_macro_flash
from app.workers.scheduler import record_duration

logger = logging.getLogger(__name__)

JOB_ID = "macro-flash"


def _run_once_sync() -> dict[str, int]:
    db = SessionLocal()
    try:
        return run_macro_flash(db)
    finally:
        db.close()


async def run_macro_flash_job() -> None:
    t0 = time.time()
    try:
        stats = await run_in_threadpool(_run_once_sync)
        if stats["fired"] or stats["failed"]:
            logger.info(
                "macro-flash: fetched=%d filtered=%d fired=%d deduped=%d failed=%d",
                stats["fetched"], stats["filtered"], stats["fired"], stats["deduped"], stats["failed"],
            )
    finally:
        record_duration(JOB_ID, int((time.time() - t0) * 1000))


def register(sched: AsyncIOScheduler) -> None:
    """1 min 轮询。MCP 无限免费，频次提到 1min 几乎零成本，
    平均延迟降到 30s（MCP 没有 push 订阅能力，必须轮询）。"""
    sched.add_job(
        run_macro_flash_job,
        trigger=IntervalTrigger(minutes=1, timezone="UTC"),
        id=JOB_ID,
        name="宏观快讯快通道（1min，源标重要 + 关键词命中直推）",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )
