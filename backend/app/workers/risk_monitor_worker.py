"""Independent deterministic risk alerts. No AI calls and no trading writes."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from apscheduler.triggers.interval import IntervalTrigger
from fastapi.concurrency import run_in_threadpool

from app.config import settings
from app.db import SessionLocal, reset_current_username, set_current_username
from app.services.notify import send_bark
from app.services.risk_monitor import deliver, evaluate, load_state, save_state

JOB_ID = "portfolio-risk-monitor"


def run_once() -> None:
    if not settings.risk_monitor_enabled:
        return
    # Manual job triggers can inherit an HTTP tenant's ContextVar. This monitor
    # always belongs to the configured owner, including its database and Bark.
    token = set_current_username(settings.auth_username)
    try:
        _run_owner_once()
    finally:
        reset_current_username(token)


def _run_owner_once() -> None:
    with SessionLocal() as db:
        state = load_state(db)
        state.update(last_poll=time.time(), configured=bool(settings.bark_device_key), enabled=True)
        save_state(db, state)
        try:
            from app.longbridge.client import get_quote_context, get_trade_context
            from app.services.risk_monitor_collector import collect

            trade = get_trade_context()
            try:
                quotes = get_quote_context()
            except Exception:
                quotes = None
            snapshot = collect(state, trade, quotes, datetime.now(timezone.utc), settings.risk_monitor_usd_hkd)
            signals, state = evaluate(snapshot, state)
            state.update(last_success=time.time(), error_type=None, coverage_issues=snapshot["issues"])
            deliver(signals, state, time.time(), send_bark)
        except Exception as exc:
            # Never publish raw exceptions: upstream errors can contain authenticated URLs.
            state["error_type"] = type(exc).__name__
            signal = {
                "key": f"monitor-error:{datetime.now(timezone.utc):%Y-%m-%d}",
                "level": 2,
                "title": "🔴 账户监控读取失败",
                "body": "未取得最新券商数据，请检查令牌或连接。旧数据不会当成实时。",
            }
            deliver([signal, *state.get("pending", {}).values()], state, time.time(), send_bark)
        save_state(db, state)


async def run_monitor() -> None:
    await run_in_threadpool(run_once)


def register(sched) -> None:
    sched.add_job(
        run_monitor,
        trigger=IntervalTrigger(seconds=60, timezone="UTC"),
        id=JOB_ID,
        name="账户与持仓异常告警（60s，无AI）",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )
    if not settings.risk_monitor_enabled:
        sched.pause_job(JOB_ID)
