from types import SimpleNamespace
from unittest.mock import patch

from app.services import position_analysis as pa


def _pos(symbol, mv, currency="USD"):
    return SimpleNamespace(
        symbol=symbol, name=symbol, quantity=10, cost_price=1.0,
        current_price=1.0, market_value=mv, currency=currency,
        unrealized_pnl=0.0, unrealized_pnl_ratio=0.0, day_pnl_ratio=0.0,
    )


def test_select_heavy_picks_above_threshold_sorted_desc():
    account = SimpleNamespace(net_assets=1000.0)
    positions = [
        _pos("AAA.US", 600), _pos("BBB.US", 300),
        _pos("CCC.US", 50), _pos("DDD.US", 10),
    ]
    # fx 用 identity:HKD 市值 == market_value
    with patch.object(pa.fx_service, "to_hkd", side_effect=lambda v, ccy, db=None: v):
        heavy = pa.select_heavy_positions(positions, account, db=None, top_n=5, min_pct=5.0)
    syms = [p["symbol"] for p in heavy]
    assert syms == ["AAA.US", "BBB.US", "CCC.US"]  # DDD 仅 1% 被剔


def test_select_heavy_excludes_options():
    account = SimpleNamespace(net_assets=1000.0)
    positions = [_pos("AAA.US", 600), _pos("MSFT260627C430000.US", 400)]
    with patch.object(pa.fx_service, "to_hkd", side_effect=lambda v, ccy, db=None: v):
        heavy = pa.select_heavy_positions(positions, account, db=None, top_n=5, min_pct=5.0)
    assert [p["symbol"] for p in heavy] == ["AAA.US"]


def test_select_heavy_fallback_to_top_n_when_none_meet_threshold():
    account = SimpleNamespace(net_assets=100000.0)  # 所有仓位占比都 <5%
    positions = [_pos("AAA.US", 600), _pos("BBB.US", 300), _pos("CCC.US", 50)]
    with patch.object(pa.fx_service, "to_hkd", side_effect=lambda v, ccy, db=None: v):
        heavy = pa.select_heavy_positions(positions, account, db=None, top_n=2, min_pct=5.0)
    assert [p["symbol"] for p in heavy] == ["AAA.US", "BBB.US"]  # 兜底取市值前 2


def test_parse_analysis_json_plain():
    raw = '{"overall_stance": "持", "per_position": [], "alerts": ["a"], "summary": "s"}'
    out = pa._parse_analysis_json(raw)
    assert out["summary"] == "s"
    assert out["alerts"] == ["a"]


def test_parse_analysis_json_strips_code_fence():
    raw = '```json\n{"summary": "x", "alerts": [], "per_position": [], "overall_stance": "攻"}\n```'
    out = pa._parse_analysis_json(raw)
    assert out["summary"] == "x"
    assert out["overall_stance"] == "攻"


def test_parse_analysis_json_invalid_returns_degraded():
    out = pa._parse_analysis_json("not json at all")
    assert out["degraded"] is True
    assert "解析失败" in out["summary"]
    assert out["per_position"] == []
