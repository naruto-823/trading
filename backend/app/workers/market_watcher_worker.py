"""市场观察 worker

每 60s 跑一次：
1. 取所有 enabled 的 alert
2. 拿涉及标的的实时价（仅美股；HK/ETF 走 fallback）
3. 对每条规则判定，命中且过冷却期 → 推 Telegram + 更新 last_triggered_at

为啥只搞美股实时：HK 实时报价依赖长桥（5min 同步频率即可），不在 60s 轮询里。
"""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi.concurrency import run_in_threadpool

from app.db import SessionLocal
from app.models.alert import Alert
from app.services.alerts import (
    check_condition,
    format_trigger_message,
    is_in_cooldown,
    list_active_alerts,
    mark_triggered,
)
from app.services.notify import send_telegram
from app.services.yahoo_quote import fetch_yahoo_quotes
from app.workers.scheduler import record_duration

logger = logging.getLogger(__name__)

JOB_ID = "market-watcher"


def _run_once_sync() -> int:
    """同步实现，返回触发次数。run_in_threadpool 调用。"""
    db = SessionLocal()
    fired = 0
    try:
        alerts: list[Alert] = list_active_alerts(db)
        if not alerts:
            return 0

        us_symbols = sorted({a.symbol for a in alerts if a.symbol.endswith(".US")})
        quotes = fetch_yahoo_quotes(us_symbols) if us_symbols else {}

        for alert in alerts:
            if is_in_cooldown(alert):
                continue
            q = quotes.get(alert.symbol)
            if not q:
                # 非美股或抓取失败：跳过，避免噪声
                continue
            current_price = q.get("regular_market_price") or q.get("post_market_price") or 0
            prev_close = q.get("previous_close") or 0
            if not check_condition(alert.condition, alert.threshold, current_price, prev_close):
                continue

            # 命中 → 通知 + 标记
            msg = format_trigger_message(alert, current_price, prev_close)
            result = send_telegram(msg)
            if result["ok"]:
                mark_triggered(db, alert)
                fired += 1
                logger.info("alert fired: %s %s @ %.2f", alert.symbol, alert.condition, current_price)
            else:
                logger.warning(
                    "alert %s 命中但 Telegram 推送失败：%s",
                    alert.symbol, result["detail"],
                )
        return fired
    finally:
        db.close()


async def run_market_watcher() -> None:
    t0 = time.time()
    try:
        fired = await run_in_threadpool(_run_once_sync)
        if fired:
            logger.info("market-watcher fired %d alerts", fired)
    finally:
        record_duration(JOB_ID, int((time.time() - t0) * 1000))


def register(sched: AsyncIOScheduler) -> None:
    """每 60s 跑一次。worker 内部自己判断有没有规则要查，没有就空跑（约 1ms）"""
    sched.add_job(
        run_market_watcher,
        trigger=IntervalTrigger(seconds=60, timezone="UTC"),
        id=JOB_ID,
        name="价格告警监控（60s）",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )
