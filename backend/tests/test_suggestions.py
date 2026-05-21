from datetime import datetime
from unittest.mock import patch

from app.models.suggestion import Suggestion
from app.services import suggestions as sug_service


def _seed_batch(db):
    row = Suggestion(
        row_id="seed1", batch_id="seedbatch", generated_at=datetime.utcnow(),
        summary="种子批次", suggestion_key="GOOG.US-buy", action="buy",
        symbol="GOOG.US", urgency="medium", thesis="种子建议",
    )
    db.add(row)
    db.commit()


def test_build_suggestions_no_refresh_returns_latest_without_regen(db_session):
    _seed_batch(db_session)
    with patch.object(sug_service, "list_positions", return_value=[object()]), \
         patch.object(sug_service, "get_latest_account", return_value=object()), \
         patch.object(sug_service, "_call_opus") as mock_opus:
        resp = sug_service.build_suggestions(db_session, force_refresh=False)
    mock_opus.assert_not_called()
    assert resp["cache_hit"] is True
    assert resp["summary"] == "种子批次"


def test_build_suggestions_no_refresh_no_batch_returns_empty(db_session):
    with patch.object(sug_service, "list_positions", return_value=[object()]), \
         patch.object(sug_service, "get_latest_account", return_value=object()), \
         patch.object(sug_service, "_call_opus") as mock_opus:
        resp = sug_service.build_suggestions(db_session, force_refresh=False)
    mock_opus.assert_not_called()
    assert resp["suggestions"] == []
    assert "尚未生成" in resp["summary"]


# —— 期权可行性护栏 ——

def test_parse_option_symbol():
    p = sug_service._parse_option_symbol("MSFT260627C430000.US")
    assert p == {"underlying": "MSFT.US", "type": "call", "strike": 430.0}
    p2 = sug_service._parse_option_symbol("META260605P590000.US")
    assert p2["type"] == "put" and p2["strike"] == 590.0
    assert sug_service._parse_option_symbol("MSFT.US") is None  # 正股不是期权


def test_option_feasibility_drops_uncovered_call():
    # 持仓 50 股,写 1 张 covered call 需 100 股 → 剔除
    sugs = [
        {"symbol": "MSFT260627C430000.US", "qty": "-1", "thesis": "covered call"},
        {"symbol": "GOOG.US", "qty": "5", "thesis": "买 GOOG"},
    ]
    held = [{"symbol": "MSFT.US", "数量": 50}]
    sug_service._check_option_feasibility(sugs, held, buy_power_hkd=10_000_000)
    assert len(sugs) == 1
    assert sugs[0]["symbol"] == "GOOG.US"  # 正股 buy 建议保留


def test_option_feasibility_keeps_covered_call_with_enough_shares():
    # 持仓 200 股,写 1 张 covered call → 可行,保留
    sugs = [{"symbol": "MSFT260627C430000.US", "qty": "-1", "thesis": "covered call"}]
    held = [{"symbol": "MSFT.US", "数量": 200}]
    sug_service._check_option_feasibility(sugs, held, buy_power_hkd=10_000_000)
    assert len(sugs) == 1


def test_option_feasibility_flags_underfunded_put():
    # 写现金担保 put,行权 400 → 需 $40000 ≈ HK$31万,购买力只 1000 → 标记不剔除
    sugs = [{
        "symbol": "MSFT260627P400000.US", "qty": "-1",
        "thesis": "现金担保 put", "data_points": ["dp1"],
    }]
    sug_service._check_option_feasibility(sugs, [], buy_power_hkd=1000)
    assert len(sugs) == 1  # put 不剔除
    assert "资金不足" in sugs[0]["data_points"][0]
