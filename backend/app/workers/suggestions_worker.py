"""建议批次定时预生成 worker —— 每天 2 次(美股盘前 13:00 UTC / 收盘后 22:00 UTC)

build_suggestions(force_refresh=True) 含辩论复核,耗时数分钟,放后台跑;
用户按需打开建议页永远命中 worker 产出的最新批次,不内联等待。

spec: docs/superpowers/specs/2026-05-20-debate-scorer-phase2-design.md
"""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi.concurrency import run_in_threadpool

from app.db import SessionLocal
from app.services.suggestions import build_suggestions
from app.workers.scheduler import record_duration

logger = logging.getLogger(__name__)

JOB_ID = "suggestions-refresh"


def _run_once_sync() -> dict:
    db = SessionLocal()
    try:
        return build_suggestions(db, force_refresh=True)
    finally:
        db.close()


async def run_suggestions_job() -> None:
    t0 = time.time()
    try:
        result = await run_in_threadpool(_run_once_sync)
        logger.info("suggestions-refresh: 生成 %d 条建议", len(result.get("suggestions", [])))
    except Exception as exc:
        logger.error("suggestions-refresh failed: %s", exc, exc_info=True)
    finally:
        record_duration(JOB_ID, int((time.time() - t0) * 1000))


def register(sched: AsyncIOScheduler) -> None:
    """每天 2 次:13:00 UTC(美股盘前)、22:00 UTC(收盘后)。
    build_suggestions 含辩论耗时数分钟,misfire_grace_time 给足 10 分钟。"""
    sched.add_job(
        run_suggestions_job,
        trigger=CronTrigger(hour="13,22", minute=0, timezone="UTC"),
        id=JOB_ID,
        name="建议批次定时预生成(美股盘前+收盘后)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
