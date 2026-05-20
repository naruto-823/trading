"""检查长桥返回的 prev_close 值是否正确"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timezone
from app.longbridge.client import get_quote_context
from app.services.quote import _us_session, _et_now

# 测试标的
test_symbols = ["AAPL.US", "MSFT.US", "NVDA.US"]

now_utc = datetime.now(timezone.utc)
et_time = _et_now(now_utc)
session = _us_session(now_utc)

print(f"当前时间:")
print(f"  UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"  ET:  {et_time.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"  交易时段: {session}")
print()

print("长桥返回的 prev_close 值:")
print("-" * 60)

try:
    ctx = get_quote_context()
    quotes = ctx.quote(test_symbols)

    for q in quotes:
        print(f"\n标的: {q.symbol}")
        print(f"  last_done (常规盘最新价): ${q.last_done}")
        print(f"  prev_close (昨收): ${q.prev_close}")

        if hasattr(q, "pre_market_quote") and q.pre_market_quote:
            print(f"  pre_market (盘前): ${q.pre_market_quote.last_done}")
        if hasattr(q, "post_market_quote") and q.post_market_quote:
            print(f"  post_market (盘后): ${q.post_market_quote.last_done}")

        print(f"\n  ✓ prev_close 应该是: 上一个交易日 16:00 ET 的收盘价")
        print(f"  ✓ 如果现在是 {session} 时段:")
        if session in ("pre", "regular"):
            print(f"     prev_close 应该是昨天(或上周五)的 16:00 ET 收盘价")
        elif session in ("post", "overnight"):
            print(f"     prev_close 应该是今天的 16:00 ET 收盘价")
        else:
            print(f"     市场休市")

except Exception as e:
    print(f"获取报价失败: {e}")
    import traceback
    traceback.print_exc()
