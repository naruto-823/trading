"""MSFT 实时盯盘脚本 - 每分钟打印价格、振幅、是否创新低/新高。

用法：
    cd backend && .venv/bin/python scripts/watch_msft.py
    # 或自定义标的、刷新间隔
    cd backend && .venv/bin/python scripts/watch_msft.py MSFT.US META.US 30
按 Ctrl+C 退出。
"""

import os
import sys
import time
from datetime import datetime, timezone, timedelta

# 让脚本能 import app.* 模块
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_THIS_DIR)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.longbridge.client import get_quote_context, reset_quote_context

# ANSI 颜色
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"

def _et_now() -> datetime:
    """美东时间（粗略处理 DST：3-11月按 EDT -4，其他按 EST -5）"""
    utc_now = datetime.now(timezone.utc)
    offset = -4 if 3 <= utc_now.month <= 11 else -5
    return utc_now + timedelta(hours=offset)

def _fmt_pct(pct: float) -> str:
    color = GREEN if pct > 0 else (RED if pct < 0 else "")
    sign = "+" if pct > 0 else ""
    return f"{color}{sign}{pct:>6.2f}%{RESET}"

def _fmt_price(price: float, prev_close: float) -> str:
    if prev_close <= 0:
        return f"{price:>8.2f}"
    color = GREEN if price > prev_close else (RED if price < prev_close else "")
    return f"{color}{BOLD}{price:>8.2f}{RESET}"

def watch(symbols: list[str], interval_sec: int = 60) -> None:
    # 每个标的的会话内最高/最低价（用于判断是否创新极值）
    session_high: dict[str, float] = {}
    session_low: dict[str, float] = {}

    # 表头
    et_open = _et_now().replace(hour=9, minute=30, second=0, microsecond=0)
    et_close = _et_now().replace(hour=16, minute=0, second=0, microsecond=0)
    print(f"{CYAN}{BOLD}=== MSFT 盯盘脚本 ==={RESET}")
    print(f"标的: {', '.join(symbols)} | 刷新间隔: {interval_sec}s | 美股盘中: {et_open.strftime('%H:%M')}-{et_close.strftime('%H:%M')} ET")
    print(f"{'时间(本地)':<10} {'美东时间':<10} | " + " | ".join(
        f"{s:<10}" for s in symbols
    ))
    print("-" * (24 + 30 * len(symbols)))

    while True:
        try:
            ctx = get_quote_context()
            quotes = ctx.quote(symbols)
        except Exception as exc:
            print(f"{RED}查询失败: {exc}，5秒后重试...{RESET}")
            try:
                reset_quote_context()
            except Exception:
                pass
            time.sleep(5)
            continue

        local_t = datetime.now().strftime("%H:%M:%S")
        et_t = _et_now().strftime("%H:%M:%S")

        cells = []
        for q in quotes:
            sym = str(q.symbol)
            last = float(q.last_done) if q.last_done else 0.0
            prev = float(q.prev_close) if q.prev_close else 0.0
            day_high = float(q.high) if q.high else 0.0
            day_low = float(q.low) if q.low else 0.0

            pct = (last - prev) / prev * 100 if prev else 0.0
            amp = (day_high - day_low) / prev * 100 if prev else 0.0  # 振幅 = (高-低)/昨收

            # 检测新极值
            prev_high = session_high.get(sym, 0.0)
            prev_low = session_low.get(sym, float("inf"))
            tag = ""
            if last > prev_high and prev_high > 0:
                tag = f" {GREEN}{BOLD}↑NEW HIGH{RESET}"
            elif last < prev_low and prev_low < float("inf"):
                tag = f" {RED}{BOLD}↓NEW LOW{RESET}"

            session_high[sym] = max(prev_high, last) if prev_high else last
            session_low[sym] = min(prev_low, last) if prev_low < float("inf") else last

            cell = (
                f"{sym:<10} {_fmt_price(last, prev)} {_fmt_pct(pct)} "
                f"日内[{day_low:.2f}-{day_high:.2f}] 振幅{amp:.2f}%{tag}"
            )
            cells.append(cell)

        print(f"{local_t} {et_t} | " + " | ".join(cells))
        time.sleep(interval_sec)

def _parse_args() -> tuple[list[str], int]:
    args = sys.argv[1:]
    if not args:
        return ["MSFT.US"], 60

    # 最后一个参数如果是数字，当作刷新间隔
    interval = 60
    if args and args[-1].isdigit():
        interval = int(args[-1])
        args = args[:-1]

    if not args:
        args = ["MSFT.US"]

    return args, interval

if __name__ == "__main__":
    syms, sec = _parse_args()
    try:
        watch(syms, sec)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}已退出{RESET}")
