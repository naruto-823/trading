"""测试 Nasdaq API 获取夜盘数据"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.yahoo_quote import fetch_yahoo_quotes, pick_latest_price

# 测试获取美股报价
print("=== 测试 Nasdaq API ===")
test_symbols = ["AAPL.US", "MSFT.US", "TSLA.US"]
print(f"测试标的: {test_symbols}")

try:
    quotes = fetch_yahoo_quotes(test_symbols, with_extended=False)
    print(f"\n成功获取 {len(quotes)} 只股票数据:")

    for symbol, data in quotes.items():
        latest_price, session = pick_latest_price(data)
        print(f"\n{symbol}:")
        print(f"  常规盘价格: ${data.get('regular_market_price', 0):.2f}")
        print(f"  盘后价格: ${data.get('post_market_price', 0):.2f}")
        print(f"  最新价格: ${latest_price:.2f} ({session})")

    print("\n✓ Nasdaq API 工作正常")
except Exception as e:
    print(f"\n✗ Nasdaq API 调用失败: {e}")
