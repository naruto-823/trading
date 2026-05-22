"""_pending_ipo_from_flows 单元测试。

回归重点：长桥 account_balance 不含 IPO 申购冻结的钱 —— 申购款已离开 total_cash，
新股配发上市前也不在 positions 里，这笔钱会从净资产/现金里整笔消失。
（实例：2723.HK 申购占款 HK$280,298.59，导致净资产少算 ~28 万。）

已了结的 IPO（有 Refund/Recovery/Allotted 流水）即便残留 -99 手续费也不算占款。
"""

from dataclasses import dataclass

import pytest

from app.longbridge.sync import _pending_ipo_from_flows

FX = {"USD_HKD": 8.0}


@dataclass
class FakeFlow:
    description: str
    balance: float
    currency: str = "HKD"


def test_pending_ipo_subscription_counted():
    """已申购、未配发、无退款流水 → 占款计入。"""
    flows = [FakeFlow("IPO 2723.HK @280,298.59 (5000 Shares) Financing: 0.00", -280298.59)]
    assert _pending_ipo_from_flows(flows, held_symbols=set(), fx_rates=FX) == pytest.approx(280298.59)


def test_allotted_ipo_in_positions_excluded():
    """新股已配发进持仓 → 不算占款（钱已变成持仓市值）。"""
    flows = [FakeFlow("IPO 2723.HK @280,298.59 (5000 Shares)", -280298.59)]
    assert _pending_ipo_from_flows(flows, held_symbols={"2723.HK"}, fx_rates=FX) == 0.0


def test_resolved_ipo_with_fee_residue_excluded():
    """已了结的 IPO（有 Refund/Recovery 流水）—— 即便净额残留 -99 手续费，也不算占款。"""
    flows = [
        FakeFlow("IPO 6871.HK @3,080,759.26 (100000 Shares) Financing: 2,772,683.33", -3080759.26),
        FakeFlow("IPO 6871.HK @3,080,759.26 (100000 Shares) Financing: 2,772,683.33", 2772683.33),
        FakeFlow("IPO 6871.HK Application Fee", -99.00),
        FakeFlow("IPO 6871.HK Financing Amount Recovery", -2772683.33),
        FakeFlow("IPO 6871.HK Application Amount Refund", 3080759.26),
    ]
    # 净额 = -99，但有 Refund/Recovery → 已了结 → 不算占款
    assert _pending_ipo_from_flows(flows, held_symbols=set(), fx_rates=FX) == 0.0


def test_allotted_marker_excluded():
    """有 Allotted 流水 = 已配发了结，即便该标的还没出现在持仓里也不算占款。"""
    flows = [
        FakeFlow("IPO 1879.HK @1,942,999.51 (10500 Shares) Financing: 1,748,699.56", -1942999.51),
        FakeFlow("IPO 1879.HK @1,942,999.51 (10500 Shares) Financing: 1,748,699.56", 1748699.56),
        FakeFlow("IPO  1879.HK Allotted Amount (15 Shares @HKD 2,748.00)", -2775.71),
        FakeFlow("IPO 1879.HK Financing Amount Recovery", -1748699.56),
        FakeFlow("IPO 1879.HK Application Amount Refund", 1942999.51),
    ]
    assert _pending_ipo_from_flows(flows, held_symbols=set(), fx_rates=FX) == 0.0


def test_financed_pending_ipo_counts_cash_portion():
    """带融资的未了结申购：只占用自有现金部分（申购额 - 融资额 + 手续费）。"""
    flows = [
        FakeFlow("IPO 8888.HK @1,000,000.00 (10000 Shares) Financing: 700,000.00", -1000000.0),
        FakeFlow("IPO 8888.HK @1,000,000.00 (10000 Shares) Financing: 700,000.00", 700000.0),
        FakeFlow("IPO 8888.HK Application Fee", -99.0),
    ]
    assert _pending_ipo_from_flows(flows, held_symbols=set(), fx_rates=FX) == pytest.approx(300099.0)


def test_non_ipo_flows_ignored():
    """普通买卖 / 分红流水不算占款。"""
    flows = [
        FakeFlow("MSFT", -16802.0, "USD"),
        FakeFlow("AAPL.US Cash Dividend: 0.27 USD per Share", 5.4, "USD"),
    ]
    assert _pending_ipo_from_flows(flows, held_symbols=set(), fx_rates=FX) == 0.0


def test_usd_ipo_converted_to_hkd():
    """美股 IPO 占款按 USD_HKD 折成 HKD。"""
    flows = [FakeFlow("IPO ABCD.US @1,000.00", -1000.0, "USD")]
    assert _pending_ipo_from_flows(flows, held_symbols=set(), fx_rates=FX) == pytest.approx(8000.0)


def test_empty():
    assert _pending_ipo_from_flows([], held_symbols=set(), fx_rates=FX) == 0.0
