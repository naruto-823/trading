from unittest.mock import patch

from apscheduler.schedulers.background import BackgroundScheduler

from app.workers import hourly_position_analysis_worker as w


def test_register_adds_job_when_enabled():
    sched = BackgroundScheduler(timezone="UTC")
    with patch.object(w.settings, "hourly_analysis_enabled", True):
        w.register(sched)
    assert sched.get_job(w.JOB_ID) is not None


def test_register_skips_job_when_disabled():
    sched = BackgroundScheduler(timezone="UTC")
    with patch.object(w.settings, "hourly_analysis_enabled", False):
        w.register(sched)
    assert sched.get_job(w.JOB_ID) is None
