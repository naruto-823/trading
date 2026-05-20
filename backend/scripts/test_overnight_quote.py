"""测试夜盘时段获取实时报价"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timezone, timedelta
from app.services.quote import _us_session, _et_now

# 测试时段判断
print("=== 测试时段判断 ===")
now_utc = datetime.now(timezone.utc)
et_time = _et_now(now_utc)
session = _us_session(now_utc)
print(f"当前 UTC 时间: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"当前美东时间: {et_time.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"当前时段: {session}")

# 模拟夜盘时段（美东时间 21:00）
print("\n=== 模拟夜盘时段 ===")
# 美东 21:00 = UTC 01:00 或 02:00（取决于夏令时）
mock_et_21 = et_time.replace(hour=21, minute=0, second=0)
# 转回 UTC
is_dst = (et_time.utcoffset().total_seconds() / 3600) == -4
mock_utc = mock_et_21 + timedelta(hours=4 if is_dst else 5)
mock_session = _us_session(mock_utc)
print(f"模拟美东时间: {mock_et_21.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"对应 UTC 时间: {mock_utc.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"判断时段: {mock_session}")
print(f"✓ 夜盘识别正确" if mock_session == "overnight" else f"✗ 预期 overnight，实际 {mock_session}")
