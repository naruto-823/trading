from unittest.mock import patch

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.workers import suggestions_worker


def test_register_adds_job():
    sched = AsyncIOScheduler()
    suggestions_worker.register(sched)
    job = sched.get_job("suggestions-refresh")
    assert job is not None
    assert job.name


async def test_run_suggestions_job_swallows_success(monkeypatch):
    monkeypatch.setattr(
        suggestions_worker, "_run_once_sync", lambda: {"suggestions": [1, 2, 3]}
    )
    # 不抛异常即通过
    await suggestions_worker.run_suggestions_job()
