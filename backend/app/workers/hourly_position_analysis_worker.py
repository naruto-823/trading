"""每小时仓位体检 worker —— 每整点(24×7)跑一次

generate_hourly_analysis 含新闻抓取 + web_search + Anthropic 调用,耗时数十秒,
用 run_in_threadpool 避免阻塞 event loop。受 settings.hourly_analysis_enabled 开关控制。

spec: docs/superpowers/specs/2026-06-10-hourly-position-analysis-design.md
"""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi.concurrency import run_in_threadpool

from app.config import settings
from app.db import SessionLocal
from app.services.position_analysis import generate_hourly_analysis
from app.workers.scheduler import record_duration

logger = logging.getLogger(__name__)

JOB_ID = "hourly-position-analysis"


def _run_once_sync() -> dict:
    db = SessionLocal()
    try:
        return generate_hourly_analysis(db)
    finally:
        db.close()


async def run_hourly_analysis_job() -> None:
    t0 = time.time()
    try:
        result = await run_in_threadpool(_run_once_sync)
        logger.info(
            "hourly-position-analysis: 已生成报告 push=%s degraded=%s",
            result.get("push_status"), result.get("degraded"),
        )
    except Exception as exc:
        logger.error("hourly-position-analysis failed: %s", exc, exc_info=True)
    finally:
        record_duration(JOB_ID, int((time.time() - t0) * 1000))


def register(sched: BaseScheduler) -> None:
    """每整点(minute=0)跑一次。enabled=False 时不挂 job。"""
    if not settings.hourly_analysis_enabled:
        logger.info("hourly-position-analysis 已禁用 (HOURLY_ANALYSIS_ENABLED=false),跳过注册")
        return
    sched.add_job(
        run_hourly_analysis_job,
        trigger=CronTrigger(minute=0, timezone="UTC"),
        id=JOB_ID,
        name="每小时仓位体检(分析+调研+指导)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
