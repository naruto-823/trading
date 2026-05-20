import json
from datetime import datetime, timedelta
from unittest.mock import patch

from app.models.event_notification import EventNotification
from app.services import debate_queue


def _debating_row(db, **over):
    row = EventNotification(
        id=over.get("id", "ev1"),
        event_hash=over.get("event_hash", "h1"),
        notified_at=over.get("notified_at", datetime.utcnow()),
        symbol="INTW",
        importance="high",
        title="英特尔大涨",
        body="英特尔美股盘前涨超4%",
        source_title="[jin10] 英特尔大涨",
        push_status="debating",
        relevance="direct", relevance_score=55, relevance_reason="点名持仓",
        sentiment="neutral", direction="neutral", confidence=50,
        affected_tickers_json='["INTW"]',
    )
    db.add(row)
    db.commit()
    return row


_VERDICT = {
    "relevance": "direct", "score": 72, "sentiment": "positive",
    "direction": "bullish", "confidence": 65, "affected_tickers": ["INTW"],
    "reason": "板块反弹", "bull_case": "贸易缓和", "bear_case": "2x decay",
    "judge_reasoning": "多方更扎实", "winning_side": "bull", "model": "debate",
}


def test_process_escalated_event_pushes_and_updates(db_session):
    _debating_row(db_session)
    with patch.object(debate_queue, "SessionLocal", return_value=db_session), \
         patch.object(debate_queue, "run_debate", return_value=_VERDICT), \
         patch.object(debate_queue, "build_position_context", return_value="ctx"), \
         patch.object(debate_queue, "send_bark", return_value={"ok": True}) as mock_bark:
        debate_queue.process_escalated_event("ev1")

    mock_bark.assert_called_once()
    row = db_session.query(EventNotification).filter_by(id="ev1").first()
    assert row.push_status == "sent"
    assert row.relevance_score == 72
    assert row.direction == "bullish"
    assert json.loads(row.debate_json)["winning_side"] == "bull"


def test_process_escalated_event_low_score_no_push(db_session):
    _debating_row(db_session)
    low = {**_VERDICT, "score": 20}
    with patch.object(debate_queue, "SessionLocal", return_value=db_session), \
         patch.object(debate_queue, "run_debate", return_value=low), \
         patch.object(debate_queue, "build_position_context", return_value="ctx"), \
         patch.object(debate_queue, "send_bark") as mock_bark:
        debate_queue.process_escalated_event("ev1")

    mock_bark.assert_not_called()
    row = db_session.query(EventNotification).filter_by(id="ev1").first()
    assert row.push_status == "skipped_low_relevance"


def test_process_escalated_event_ignores_non_debating_row(db_session):
    row = _debating_row(db_session)
    row.push_status = "sent"
    db_session.commit()
    with patch.object(debate_queue, "SessionLocal", return_value=db_session), \
         patch.object(debate_queue, "run_debate") as mock_run:
        debate_queue.process_escalated_event("ev1")
    mock_run.assert_not_called()


def test_reconcile_stale_debates_finalizes_zombie(db_session):
    _debating_row(db_session, notified_at=datetime.utcnow() - timedelta(minutes=10))
    with patch.object(debate_queue, "send_bark", return_value={"ok": True}):
        n = debate_queue.reconcile_stale_debates(db_session)
    assert n == 1
    row = db_session.query(EventNotification).filter_by(id="ev1").first()
    # relevance_score=55 ≥ 阈值50 → 用 triage 分推送收尾
    assert row.push_status == "sent"


def test_reconcile_skips_fresh_debating_row(db_session):
    _debating_row(db_session, notified_at=datetime.utcnow())
    n = debate_queue.reconcile_stale_debates(db_session)
    assert n == 0
    row = db_session.query(EventNotification).filter_by(id="ev1").first()
    assert row.push_status == "debating"


def test_format_debate_push_layout(db_session):
    row = _debating_row(db_session)
    title, body, level = debate_queue.format_debate_push(row, _VERDICT)
    assert "🧠" in title
    assert "INTW" in title
    assert "判官:看涨" in body
    assert "多:" in body and "空:" in body
    assert level == "timeSensitive"  # importance=high
