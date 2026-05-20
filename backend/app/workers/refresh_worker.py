"""定时刷新 AI 复盘(briefing)

策略:在关键市场事件时刻强制重新生成 briefing(force_refresh=True),覆盖缓存。
- HK 开盘  01:30 UTC(北京 09:30)
- HK 收盘  08:00 UTC(北京 16:00)
- US 盘前  12:30 UTC(北京 20:30 / 美东 08:30)—— 通胀 / 财报数据高发期
- US 开盘  13:30 UTC(北京 21:30 / 美东 09:30 EDT)
- US 收盘  20:00 UTC(北京次日 04:00 / 美东 16:00 EDT)

工作日跑(周一到周五,trading hours),周末不跑。

注:AI 决策建议的定时刷新由 suggestions_worker 负责(每天 2 次),不在这里 ——
   建议刷新含辩论复核(Phase 2),只该有一个 owner。
"""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from fastapi.concurrency import run_in_threadpool

from app.db import SessionLocal
from app.services.briefing import build_briefing
from app.workers.scheduler import record_duration

logger = logging.getLogger(__name__)

JOB_ID = "ai-refresh"
BRIEFING_JOB_ID = "briefing-refresh"


async def run_briefing_refresh() -> None:
    t0 = time.time()
    db = SessionLocal()
    try:
        r = await run_in_threadpool(build_briefing, db, True)
        logger.info("briefing-refresh ok: %d stocks", len(r.get("stocks", [])))
    except Exception:
        logger.exception("briefing-refresh failed")
        raise
    finally:
        db.close()
        record_duration(BRIEFING_JOB_ID, int((time.time() - t0) * 1000))


# 5 个关键市场时刻(UTC)—— 工作日触发
KEY_HOURS_UTC = [
    ("hk_open", 1, 30),
    ("hk_close", 8, 0),
    ("us_premarket", 12, 30),
    ("us_open", 13, 30),
    ("us_close", 20, 0),
]


def register(sched: AsyncIOScheduler) -> None:
    """1 个 job + OrTrigger 多个 cron。面板里就一行。"""
    triggers = [
        CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone="UTC")
        for _tag, hour, minute in KEY_HOURS_UTC
    ]
    sched.add_job(
        run_briefing_refresh,
        trigger=OrTrigger(triggers),
        id=JOB_ID,
        name="AI 复盘刷新(工作日 5 个市场时刻)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
