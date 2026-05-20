from datetime import datetime, timezone
from unittest.mock import patch

from app.models.event_notification import EventNotification
from app.services import macro_pusher
from app.services.macro_feed import MacroFlash


def _flash():
    return MacroFlash(
        time=datetime.now(timezone.utc),
        title="英特尔美股盘前涨超4%",
        content="英特尔美股盘前涨超4%,半导体板块走强",
        importance=4,
        source="jin10",
        tags=[],
    )


_ESCALATING_TRIAGE = {
    "relevance": "direct", "score": 55, "sentiment": "positive",
    "direction": "bullish", "confidence": 60, "affected_tickers": ["INTW"],
    "reason": "点名半导体", "model": "claude-haiku-4-5-20251001",
}


def test_macro_pusher_escalates_to_debate(db_session):
    with patch.object(macro_pusher, "fetch_macro_news", return_value=[_flash()]), \
         patch.object(macro_pusher, "score_relevance", return_value=_ESCALATING_TRIAGE), \
         patch.object(macro_pusher, "submit_debate") as mock_submit, \
         patch.object(macro_pusher, "send_bark") as mock_bark:
        stats = macro_pusher.run_macro_flash(db_session)

    # 升级:落 debating 行 + submit,不直接推
    assert stats["escalated"] == 1
    mock_bark.assert_not_called()
    mock_submit.assert_called_once()
    row = db_session.query(EventNotification).filter_by(push_status="debating").first()
    assert row is not None
    assert row.relevance_score == 55
