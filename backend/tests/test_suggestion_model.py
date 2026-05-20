from datetime import datetime

from app.models.suggestion import Suggestion


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
