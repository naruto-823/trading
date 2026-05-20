"""测试系统返回的 INTW.US 报价"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.quote import get_realtime_quotes

print("=== 系统返回的报价 ===")
try:
    responses = get_realtime_quotes(["INTW.US"])

    for resp in responses:
        print(f"\n标的: {resp.symbol}")
        print(f"当前价格 (current_price): ${resp.current_price}")
        print(f"常规盘价格 (last_done): ${resp.last_done}")
        print(f"盘前价格 (pre_market_price): ${resp.pre_market_price}")
        print(f"盘后价格 (post_market_price): ${resp.post_market_price}")
        print(f"昨收 (prev_close): ${resp.prev_close}")
        print(f"交易时段: {resp.trading_session}")

        print(f"\n✓ 当前价格应该是: $361.162 (盘前)")
        print(f"✓ 实际显示: ${resp.current_price}")

        if abs(resp.current_price - 361.162) < 0.01:
            print("✓ 价格正确")
        else:
            print(f"✗ 价格错误！预期361.162，实际{resp.current_price}")

except Exception as e:
    print(f"获取报价失败: {e}")
    import traceback
    traceback.print_exc()
