from datetime import datetime

from app.models.account import AccountSnapshot
from app.models.position import Position
from app.services.risk_radar import build_risk_radar


def test_risk_radar_reports_concentration_option_and_stock_shock(db_session):
    db_session.add(AccountSnapshot(net_assets=100_000, outstanding_debt=-25_000, synced_at=datetime.now()))
    db_session.add_all([
        Position(symbol="META.US", market="US", quantity=100, market_value=5_000, currency="USD"),
        Position(symbol="META260731C660000.US", market="US", quantity=-1, market_value=0, currency="USD"),
    ])
    db_session.commit()

    result = build_risk_radar(db_session, 10)

    assert result["concentration"][0]["symbol"] == "META.US"
    assert result["concentration"][0]["weight_pct"] > 30
    assert result["stress"]["shock_pct"] == -10
    assert result["stress"]["estimated_stock_pnl_hkd"] < 0
    assert result["options"][0]["covered"] is True
    assert any(item["title"] == "融资敞口" for item in result["summary"])
