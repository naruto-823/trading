"""定时同步长桥账户数据

策略：
- 主调度按 interval 跑：白天 5 min，深夜 30 min
- 因为 sync_all 是同步 IO，用 run_in_threadpool 把它丢线程，不阻塞 event loop
- 同步状态已经写 sync_log 表，scheduler 这里再记录一份运行汇总到内存
"""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi.concurrency import run_in_threadpool

from app.db import SessionLocal
from app.longbridge.sync import sync_all
from app.workers.scheduler import record_duration

logger = logging.getLogger(__name__)

JOB_ID = "broker-sync"


async def run_broker_sync() -> None:
    """单次同步：开个 short-lived session，跑完关掉"""
    t0 = time.time()
    db = SessionLocal()
    try:
        results = await run_in_threadpool(sync_all, db)
        ok = sum(1 for r in results if r.status == "success")
        total_rows = sum(r.rows_written for r in results)
        logger.info("broker-sync ok: %d/%d kinds, %d rows", ok, len(results), total_rows)
    finally:
        db.close()
        record_duration(JOB_ID, int((time.time() - t0) * 1000))


def register(sched: AsyncIOScheduler) -> None:
    """注册两个 cron 触发器：
    - 白天高频（北京时间 09:00-翌日 05:00 = UTC 01:00-21:00）：每 5 min
    - 深夜低频（UTC 21:00-翌日 01:00）：每 30 min
    """
    # 周一到周五 + 周末也跑（盘后/夜盘可能有美股动作）
    sched.add_job(
        run_broker_sync,
        trigger=CronTrigger(minute="*/5", hour="1-20", timezone="UTC"),
        id=JOB_ID,
        name="长桥账户同步（白天 5min）",
        replace_existing=True,
        max_instances=1,        # 不让上一次没跑完时排队叠加
        coalesce=True,           # 错过的 fire 合并成一次
        misfire_grace_time=120,
    )
    sched.add_job(
        run_broker_sync,
        trigger=CronTrigger(minute="0,30", hour="21-23,0", timezone="UTC"),
        id=f"{JOB_ID}-night",
        name="长桥账户同步（深夜 30min）",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
