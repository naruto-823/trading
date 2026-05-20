"""端到端测试：模拟夜盘时段获取报价"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from app.services.quote import get_realtime_quotes, _us_session

# 模拟夜盘时段（美东 21:00）
def mock_overnight_time():
    """返回美东时间 21:00 对应的 UTC 时间"""
    now = datetime.now(timezone.utc)
    # 假设当前是夏令时，美东 21:00 = UTC 01:00
    mock_utc = now.replace(hour=1, minute=0, second=0, microsecond=0)
    return mock_utc

print("=== 端到端集成测试 ===")
test_symbols = ["AAPL.US", "MSFT.US"]

# 测试1: 验证夜盘时段判断
with patch('app.services.quote.datetime') as mock_dt:
    mock_utc = mock_overnight_time()
    mock_dt.now.return_value = mock_utc
    session = _us_session(mock_utc)
    print(f"\n1. 时段判断: {session}")
    print(f"   ✓ 正确" if session == "overnight" else f"   ✗ 错误")

print(f"\n2. 测试获取报价（模拟夜盘）")
print(f"   标的: {test_symbols}")
print(f"   说明: 由于需要长桥 SDK，此测试需要在实际环境运行")
print(f"   预期行为: 长桥数据 + Nasdaq 补充")

print("\n✓ 集成测试脚本就绪")
print("建议: 在美东 20:00 后运行实际测试")
