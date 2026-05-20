"""每日资产基线 capture worker

调度：每天北京时间 16:01（= UTC 08:01）—— HK 收盘后 1 分钟。
经验对齐：LB APP 的"当日盈亏"日切点就是 BJT 16:00（HK 收盘），所以基线必须
在 HK 收盘后第一时间抓，否则会偏。

幂等：当日基线已存在则跳过。
"""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi.concurrency import run_in_threadpool

from app.db import SessionLocal
from app.services.daily_baseline import capture_baseline
from app.workers.scheduler import record_duration

logger = logging.getLogger(__name__)

JOB_ID = "daily-baseline-capture"


def _run_once_sync() -> bool:
    db = SessionLocal()
    try:
        row = capture_baseline(db)
        return row is not None
    finally:
        db.close()


async def run_daily_baseline() -> None:
    t0 = time.time()
    try:
        ok = await run_in_threadpool(_run_once_sync)
        if ok:
            logger.info("daily-baseline-capture done")
    finally:
        record_duration(JOB_ID, int((time.time() - t0) * 1000))


def register(sched: AsyncIOScheduler) -> None:
    """每天 BJT 16:01 = UTC 08:01 触发一次（HK 刚收盘）"""
    sched.add_job(
        run_daily_baseline,
        trigger=CronTrigger(hour=8, minute=1, timezone="UTC"),
        id=JOB_ID,
        name="每日资产基线 capture（每天 BJT 16:01，HK 收盘后）",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
