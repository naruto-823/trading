"""诊断 INTW.US 数据不一致问题"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timezone
from app.services.quote import _us_session

# 1. 检查当前时段
now_utc = datetime.now(timezone.utc)
session = _us_session(now_utc)
print(f"当前时段: {session}")
print(f"UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")

# 2. 测试长桥数据
print("\n=== 长桥 SDK 数据 ===")
try:
    from app.longbridge.client import get_quote_context
    ctx = get_quote_context()
    quotes = ctx.quote(["INTW.US"])

    for q in quotes:
        print(f"标的: {q.symbol}")
        print(f"last_done: {q.last_done}")
        print(f"prev_close: {q.prev_close}")
        if hasattr(q, "pre_market_quote") and q.pre_market_quote:
            print(f"pre_market: {q.pre_market_quote.last_done}")
        if hasattr(q, "post_market_quote") and q.post_market_quote:
            print(f"post_market: {q.post_market_quote.last_done}")
except Exception as e:
    print(f"长桥数据获取失败: {e}")

# 3. 测试 Nasdaq 数据
print("\n=== Nasdaq API 数据 ===")
try:
    from app.services.yahoo_quote import fetch_yahoo_quote
    nq = fetch_yahoo_quote("INTW.US", with_extended=True)
    if nq:
        print(f"regular: ${nq['regular_market_price']}")
        print(f"post: ${nq['post_market_price']}")
        print(f"pre: ${nq['pre_market_price']}")
except Exception as e:
    print(f"Nasdaq 数据获取失败: {e}")
