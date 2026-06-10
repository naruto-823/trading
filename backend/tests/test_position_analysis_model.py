from datetime import datetime

from app.models.position_analysis_report import PositionAnalysisReport


def test_report_persists_and_reads_back(db_session):
    row = PositionAnalysisReport(
        generated_at=datetime.utcnow(),
        account_json='{"net_assets": 100}',
        positions_json='[{"symbol": "MSFT.US"}]',
        research_brief="近期 AI 资本开支上行",
        analysis_json='{"summary": "持"}',
        summary="整体持有,关注 MSFT",
        push_status="sent",
        push_detail="ok",
        degraded=False,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    assert row.id is not None
    got = db_session.get(PositionAnalysisReport, row.id)
    assert got.summary == "整体持有,关注 MSFT"
    assert got.degraded is False
