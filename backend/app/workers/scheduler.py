"""APScheduler 单例 + 状态追踪

设计：
- 进程内 AsyncIOScheduler，跟 FastAPI 同 event loop
- 每个 job 一个稳定 id，便于 /api/system/jobs 列出
- 通过事件 listener 记录 last_run / last_status / last_error 到内存
- IO 密集的 job（同步、抓新闻、调 LLM）需要用 run_in_threadpool 避免阻塞 loop

启停：
- main.py lifespan: start_scheduler() 在 startup，shutdown_scheduler() 在 exit
- uvicorn --reload 每次会重启进程，scheduler 也会重启，不会重复跑
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, JobExecutionEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)


@dataclass
class JobRunRecord:
    """单个 job 的运行状态快照"""
    last_run_at: datetime | None = None
    last_status: str = "never"  # never / success / error
    last_error: str | None = None
    last_duration_ms: int | None = None
    run_count: int = 0


# 模块单例：调度器 + 状态字典
_scheduler: AsyncIOScheduler | None = None
_run_records: dict[str, JobRunRecord] = {}


def _on_job_event(event: JobExecutionEvent) -> None:
    rec = _run_records.setdefault(event.job_id, JobRunRecord())
    rec.last_run_at = event.scheduled_run_time or datetime.now(timezone.utc)
    rec.run_count += 1
    if event.exception is not None:
        rec.last_status = "error"
        rec.last_error = f"{type(event.exception).__name__}: {event.exception}"
        logger.error("Job %s raised: %s", event.job_id, event.exception, exc_info=event.exception)
    else:
        rec.last_status = "success"
        rec.last_error = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=timezone.utc)
        _scheduler.add_listener(_on_job_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    return _scheduler


def start_scheduler() -> None:
    """在 FastAPI lifespan startup 中调用。注册 job 然后启动。"""
    sched = get_scheduler()
    if sched.running:
        return

    # 延迟 import 避免循环依赖（job 模块依赖 db / services）
    from app.workers.broker_sync_worker import register as register_broker_sync
    from app.workers.event_watcher_worker import register as register_event_watcher
    from app.workers.market_watcher_worker import register as register_market_watcher
    from app.workers.refresh_worker import register as register_refresh

    register_broker_sync(sched)
    register_refresh(sched)
    register_market_watcher(sched)
    register_event_watcher(sched)

    sched.start()
    logger.info("Scheduler started with %d jobs: %s",
                len(sched.get_jobs()),
                [j.id for j in sched.get_jobs()])


def shutdown_scheduler() -> None:
    """在 FastAPI lifespan shutdown 中调用"""
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)


def list_jobs() -> list[dict[str, Any]]:
    """供 /api/system/jobs 端点：返回所有 job 的状态"""
    sched = get_scheduler()
    out = []
    for job in sched.get_jobs():
        rec = _run_records.get(job.id, JobRunRecord())
        out.append({
            "id": job.id,
            "name": job.name or job.id,
            "next_run_at": _iso(job.next_run_time),
            "trigger": str(job.trigger),
            "last_run_at": _iso(rec.last_run_at),
            "last_status": rec.last_status,
            "last_error": rec.last_error,
            "last_duration_ms": rec.last_duration_ms,
            "run_count": rec.run_count,
        })
    return out


async def trigger_job_now(job_id: str) -> bool:
    """手动触发一次某个 job（用于调试 + 前端"立即同步"按钮）"""
    sched = get_scheduler()
    job = sched.get_job(job_id)
    if not job:
        return False
    # APScheduler 没直接的"立即跑"，用 modify next_run_time 实现
    job.modify(next_run_time=datetime.now(timezone.utc))
    return True


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def record_duration(job_id: str, duration_ms: int) -> None:
    """允许 worker 内部在 success 路径上写时长（apscheduler 的事件没带 duration）"""
    rec = _run_records.setdefault(job_id, JobRunRecord())
    rec.last_duration_ms = duration_ms
