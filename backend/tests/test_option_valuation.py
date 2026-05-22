"""期权持仓估值 _option_valuation 的单元测试。

回归重点：报价缺失（current<=0）时绝不能拿成本价反推一个假盈亏。
历史 bug —— 行情接口无权限/限流失败后，long 显示 -100%（权利金全损）、
short 显示 +100%（白赚权利金），把组合盈亏整体虚高了上千刀。
"""

import pytest

from app.longbridge.sync import _option_valuation


def test_long_option_in_profit():
    mkt, pnl, ratio = _option_valuation(qty=1, cost=10.0, current=15.0)
    assert mkt == pytest.approx(1500.0)
    assert pnl == pytest.approx(500.0)
    assert ratio == pytest.approx(0.5)


def test_long_option_in_loss():
    # PDD 270115 120 Call：成本 10.90，现价 6.50
    mkt, pnl, ratio = _option_valuation(qty=1, cost=10.90, current=6.50)
    assert mkt == pytest.approx(650.0)
    assert pnl == pytest.approx(-440.0)
    assert ratio == pytest.approx(-440.0 / 1090.0)


def test_short_option_in_profit():
    # META 260605 590 Put short：成本 17.45，现价 9.25 → 卖方赚 820
    mkt, pnl, ratio = _option_valuation(qty=-1, cost=17.45, current=9.25)
    assert mkt == pytest.approx(925.0)
    assert pnl == pytest.approx(820.0)
    assert ratio == pytest.approx(820.0 / 1745.0)


def test_short_option_in_loss():
    # MSFT 260618 440 Call short：成本 5.01，现价 6.76 → 卖方亏 175
    mkt, pnl, ratio = _option_valuation(qty=-1, cost=5.01, current=6.76)
    assert mkt == pytest.approx(676.0)
    assert pnl == pytest.approx(-175.0)
    assert ratio == pytest.approx(-175.0 / 501.0)


def test_multi_contract_short():
    mkt, pnl, ratio = _option_valuation(qty=-3, cost=5.0, current=4.0)
    assert mkt == pytest.approx(1200.0)
    assert pnl == pytest.approx(300.0)
    assert ratio == pytest.approx(0.2)


def test_missing_quote_short_no_fabricated_profit():
    """报价缺失的 short 期权：盈亏必须为 0，不能编造出 +cost_val 的假盈利。"""
    mkt, pnl, ratio = _option_valuation(qty=-1, cost=5.01, current=0.0)
    assert mkt == 0.0
    assert pnl == 0.0
    assert ratio == 0.0


def test_missing_quote_long_no_fabricated_loss():
    """报价缺失的 long 期权：盈亏必须为 0，不能编造出 -cost_val 的假亏损。"""
    mkt, pnl, ratio = _option_valuation(qty=1, cost=10.90, current=0.0)
    assert mkt == 0.0
    assert pnl == 0.0
    assert ratio == 0.0
