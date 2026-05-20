"""美股 overnight push 兜底

长桥 OpenAPI 不提供 ET 20:00-04:00 时段的 WS 推送（trading_session 列表
里没有 Overnight）。本 worker 在 overnight 时段每 5s 拉一次 Nasdaq API
报价，把每个美股持仓的 last_done 写入 realtime.store（source 标记为
"nasdaq_overnight"），让 store 在 overnight 期间也有"准实时"数据。

期权 symbol（如 MSFT260618C440000.US）走的是 OPRA 不是 NBBO，Nasdaq /info
不返回；本 worker 只覆盖股票/ETF。

非 overnight 时段：worker 空跑（无 IO），不会浪费 Nasdaq API 配额。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, time as dtime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi.concurrency import run_in_threadpool

from app.longbridge.realtime import store
from app.services.yahoo_quote import fetch_yahoo_quotes
from app.workers.scheduler import record_duration

logger = logging.getLogger(__name__)

JOB_ID = "overnight-quote-feed"
INTERVAL_SECONDS = 5


def _et_now(now_utc: datetime | None = None) -> datetime:
    """UTC → ET（自动处理夏令时）。与 app.services.quote._et_now 同口径。"""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    year = now_utc.year
    march_first = datetime(year, 3, 1, tzinfo=timezone.utc)
    dst_start = march_first + timedelta(days=(6 - march_first.weekday()) % 7 + 7)
    dst_start = dst_start.replace(hour=7)
    nov_first = datetime(year, 11, 1, tzinfo=timezone.utc)
    dst_end = nov_first + timedelta(days=(6 - nov_first.weekday()) % 7)
    dst_end = dst_end.replace(hour=6)
    is_dst = dst_start <= now_utc < dst_end
    return now_utc + timedelta(hours=-4 if is_dst else -5)


def _is_overnight_now() -> bool:
    """美股 overnight = 工作日 ET 20:00-04:00。

    注意跨日：ET 20:00-23:59 的"工作日"是前一日 ET 的工作日（周一到周五开盘），
    而 ET 00:00-04:00 是后一日早晨。两段都视为 overnight；周末（ET Sat/Sun）排除。
    """
    et = _et_now()
    # 20:00-23:59：前一日须为工作日
    if et.time() >= dtime(20, 0):
        return et.weekday() < 5  # Mon-Fri
    # 00:00-04:00：今日须为工作日（Tue-Sat 凌晨，对应前一日 Mon-Fri 收盘后延伸）
    if et.time() < dtime(4, 0):
        # weekday()==5 表示 Sat 凌晨（Fri 收盘后），算 overnight；==6 是 Sun 凌晨（无效）
        return et.weekday() <= 5  # 排除 Sun 00:00-04:00（周日凌晨）
    return False


def _collect_us_stock_symbols() -> list[str]:
    """从 store 取已订阅的美股股票（排除期权 / 港股 / A 股）"""
    syms = store.subscribed_symbols()
    # .US 后缀 + 长度 <= 8（粗略排除 OCC 期权 symbol：MSFT260618C440000.US 有 21 字符）
    return [s for s in syms if s.endswith(".US") and len(s) <= 8]


def _run_once_sync() -> int:
    """同步实现：拉 Nasdaq 报价 → 写 store。返回写入条数。"""
    if not _is_overnight_now():
        return 0
    symbols = _collect_us_stock_symbols()
    if not symbols:
        return 0

    quotes = fetch_yahoo_quotes(symbols)  # 内部并发，超时 5s
    if not quotes:
        return 0

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = 0
    for sym, q in quotes.items():
        last = q.get("post_market_price") or q.get("regular_market_price") or 0.0
        prev = q.get("previous_close") or 0.0
        if last > 0:
            store.update_from_overnight(sym, last, prev, now_iso)
            written += 1
    return written


async def run_overnight_quote_feed() -> None:
    t0 = time.time()
    try:
        written = await run_in_threadpool(_run_once_sync)
        if written:
            logger.debug("overnight-quote-feed wrote %d symbols", written)
    finally:
        record_duration(JOB_ID, int((time.time() - t0) * 1000))


def register(sched: AsyncIOScheduler) -> None:
    """每 5s 跑一次；非 overnight 时段内部直接 return（无 IO）"""
    sched.add_job(
        run_overnight_quote_feed,
        trigger=IntervalTrigger(seconds=INTERVAL_SECONDS, timezone="UTC"),
        id=JOB_ID,
        name="美股 overnight push 兜底（5s, ET 20:00-04:00 工作日才真跑）",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3,
    )
