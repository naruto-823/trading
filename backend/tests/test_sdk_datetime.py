"""_sdk_dt_to_utc 单元测试。

回归重点：longport SDK 返回的成交/订单时间是【机器本地时区的 naive datetime】，
而本应用其它地方（today 过滤、当日盈亏）一律按 UTC 处理。入库前必须转成 naive UTC，
否则在 UTC+8 机器上每条时间都会 +8h，把昨天美股盘的成交误判成"今天平仓"。
"""

import time
from datetime import datetime, timedelta, timezone

import pytest

from app.longbridge.sync import _sdk_dt_to_utc


@pytest.fixture
def tz():
    """临时切换进程时区，结束后恢复。tzset 仅 Unix 可用（macOS/Linux 均 OK）。"""
    import os

    saved = os.environ.get("TZ")

    def _set(name: str):
        os.environ["TZ"] = name
        time.tzset()

    yield _set

    if saved is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = saved
    time.tzset()


def test_none_passes_through():
    assert _sdk_dt_to_utc(None) is None


def test_aware_utc_becomes_naive_utc():
    dt = datetime(2026, 5, 20, 18, 18, 55, tzinfo=timezone.utc)
    assert _sdk_dt_to_utc(dt) == datetime(2026, 5, 20, 18, 18, 55)


def test_aware_plus8_converts_to_utc():
    dt = datetime(2026, 5, 21, 2, 18, 55, tzinfo=timezone(timedelta(hours=8)))
    assert _sdk_dt_to_utc(dt) == datetime(2026, 5, 20, 18, 18, 55)


def test_naive_in_shanghai_tz_shifts_back_8h(tz):
    """SDK 实际行为：UTC+8 机器上 naive 本地时间 → 必须 -8h 还原成 UTC。"""
    tz("Asia/Shanghai")
    # 真实案例：MSFT 成交 SDK repr 是 2026-05-20T18:18:55Z，
    # Python 属性渲染成本地 naive 2026-05-21 02:18:55
    dt = datetime(2026, 5, 21, 2, 18, 55)
    assert _sdk_dt_to_utc(dt) == datetime(2026, 5, 20, 18, 18, 55)


def test_naive_in_utc_tz_unchanged(tz):
    """机器本身就在 UTC 时区时，naive 即 UTC，不应有偏移。"""
    tz("UTC")
    dt = datetime(2026, 5, 21, 2, 18, 55)
    assert _sdk_dt_to_utc(dt) == datetime(2026, 5, 21, 2, 18, 55)
