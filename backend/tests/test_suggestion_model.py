from datetime import datetime

from app.models.suggestion import Suggestion
from app.services import suggestions as sug_service


def test_suggestion_debate_json_roundtrips(db_session):
    row = Suggestion(
        row_id="r1",
        batch_id="b1",
        generated_at=datetime.utcnow(),
        suggestion_key="INTW.US-sell",
        action="sell",
        symbol="INTW.US",
        debate_json='{"consistency": "contradict"}',
    )
    db_session.add(row)
    db_session.commit()

    got = db_session.query(Suggestion).filter_by(row_id="r1").first()
    assert got.debate_json == '{"consistency": "contradict"}'


def test_persist_and_serialize_debate(db_session):
    suggestions = [{
        "action": "sell", "symbol": "INTW.US", "qty": "16", "price": "约 302",
        "urgency": "medium", "thesis": "卖出\n⚖️ 辩论复核:…",
        "data_points": ["dp1"],
        "debate": {"consistency": "contradict", "direction": "bullish",
                   "winning_side": "bull", "confidence": 65,
                   "bull_case": "反弹", "bear_case": "", "judge_reasoning": "多方更扎实"},
    }]
    rows = sug_service._persist_batch(
        db_session, "batch1", datetime.utcnow(), "summary", suggestions
    )
    assert rows[0].debate_json is not None

    d = sug_service._row_to_dict(rows[0])
    assert d["debate"]["consistency"] == "contradict"
    assert d["debate"]["winning_side"] == "bull"
