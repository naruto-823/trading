"""测试当日盈亏基准是否使用最后交易价格"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timezone
from app.longbridge.client import get_quote_context
from app.services.quote import _us_session

test_symbol = "INTW.US"
now_utc = datetime.now(timezone.utc)
session = _us_session(now_utc)

print(f"当前交易时段: {session}")
print(f"测试标的: {test_symbol}\n")

try:
    ctx = get_quote_context()
    quotes = ctx.quote([test_symbol])

    for q in quotes:
        regular = float(q.last_done) if hasattr(q, "last_done") else 0.0
        prev_close = float(q.prev_close) if hasattr(q, "prev_close") else 0.0

        pre_q = getattr(q, "pre_market_quote", None)
        post_q = getattr(q, "post_market_quote", None)
        pre_last = float(pre_q.last_done) if pre_q and hasattr(pre_q, "last_done") and pre_q.last_done else 0.0
        post_last = float(post_q.last_done) if post_q and hasattr(post_q, "last_done") and post_q.last_done else 0.0

        print(f"长桥数据:")
        print(f"  常规盘 last_done: ${regular}")
        print(f"  prev_close: ${prev_close}")
        print(f"  盘前价: ${pre_last}")
        print(f"  盘后价: ${post_last}")

        # 计算当日盈亏基准（按新逻辑：始终用常规盘16:00收盘价）
        if session == "pre":
            baseline = regular  # 盘前：last_done是昨天16:00收盘价
            print(f"\n当日盈亏基准（盘前）: ${baseline}")
            print(f"  来源: 昨天常规盘收盘价(last_done)")
        elif session == "regular":
            baseline = prev_close  # 盘中：prev_close是昨天16:00收盘价
            print(f"\n当日盈亏基准（盘中）: ${baseline}")
            print(f"  来源: 昨天常规盘收盘价(prev_close)")
        else:  # post, overnight
            baseline = regular  # 盘后/夜盘：last_done是今天16:00收盘价
            print(f"\n当日盈亏基准（盘后/夜盘）: ${baseline}")
            print(f"  来源: 今天常规盘收盘价(last_done)")

        # 当前价格
        if session == "pre":
            current = pre_last if pre_last > 0 else regular
        elif session == "post":
            current = post_last if post_last > 0 else regular
        elif session == "regular":
            current = regular
        else:
            current = post_last or regular or pre_last

        print(f"\n当前价格: ${current}")
        print(f"当日盈亏: ${current - baseline:.2f}")

except Exception as e:
    print(f"获取报价失败: {e}")
    import traceback
    traceback.print_exc()
