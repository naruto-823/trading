"""定时刷新 AI 复盘 + 决策建议

策略：在关键市场事件时刻强制重新生成（force_refresh=True），覆盖 30 分钟缓存。
- HK 开盘  01:30 UTC（北京 09:30）
- HK 收盘  08:00 UTC（北京 16:00）
- US 盘前  12:30 UTC（北京 20:30 / 美东 08:30）—— 通胀 / 财报数据高发期
- US 开盘  13:30 UTC（北京 21:30 / 美东 09:30 EDT）
- US 收盘  20:00 UTC（北京次日 04:00 / 美东 16:00 EDT）

工作日跑（周一到周五，trading hours），周末不跑。
"""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi.concurrency import run_in_threadpool

from app.db import SessionLocal
from app.services.briefing import build_briefing
from app.services.suggestions import build_suggestions
from app.workers.scheduler import record_duration

logger = logging.getLogger(__name__)

BRIEFING_JOB_ID = "briefing-refresh"
SUGGESTIONS_JOB_ID = "suggestions-refresh"


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


async def run_suggestions_refresh() -> None:
    t0 = time.time()
    db = SessionLocal()
    try:
        r = await run_in_threadpool(build_suggestions, db, True)
        logger.info("suggestions-refresh ok: %d suggestions",
                    len(r.get("suggestions", [])))
    except Exception:
        logger.exception("suggestions-refresh failed")
        raise
    finally:
        db.close()
        record_duration(SUGGESTIONS_JOB_ID, int((time.time() - t0) * 1000))


# 五个关键时刻，5 cron 之间共用：
KEY_HOURS_UTC = [
    ("hk_open", 1, 30),
    ("hk_close", 8, 0),
    ("us_premarket", 12, 30),
    ("us_open", 13, 30),
    ("us_close", 20, 0),
]


def register(sched: AsyncIOScheduler) -> None:
    """对每个关键时刻注册 briefing + suggestions 各一个 job
    （5 时刻 × 2 内容 = 10 job，太多。改成：每个时刻一个 job，里面同时刷两个）"""
    for tag, hour, minute in KEY_HOURS_UTC:
        sched.add_job(
            _run_both,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=hour,
                minute=minute,
                timezone="UTC",
            ),
            id=f"refresh-{tag}",
            name=f"AI 复盘+建议刷新（{tag}）",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=600,  # 错过 10min 内还跑
        )


async def _run_both() -> None:
    """一次性把 briefing + suggestions 都刷了（共享市场背景抓取的开销低）"""
    await run_briefing_refresh()
    await run_suggestions_refresh()
