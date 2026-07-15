from unittest.mock import patch

from app.models.event_notification import EventNotification
from app.services import news_pipeline as np
from app.workers import jin10_browser_worker as jw

_ESCALATING_TRIAGE = {
    "relevance": "direct", "score": 55, "sentiment": "positive",
    "direction": "bullish", "confidence": 60, "affected_tickers": ["MSFT"],
    "reason": "点名微软", "model": "claude-haiku-4-5-20251001",
}


def test_jin10_flash_escalates_to_debate(db_session):
    flash = {"text": "微软发布 AI 新品,股价异动", "id": "999", "is_important": True}
    # 逻辑已抽到 news_pipeline,patch 那里;入口仍走 jin10 worker 验证委托
    with patch.object(np, "SessionLocal", return_value=db_session), \
         patch.object(np, "score_relevance", return_value=_ESCALATING_TRIAGE), \
         patch.object(np, "submit_debate") as mock_submit, \
         patch.object(np, "send_bark") as mock_bark:
        jw._process_flash_sync(flash)

    mock_bark.assert_not_called()
    mock_submit.assert_called_once()
    row = db_session.query(EventNotification).filter_by(push_status="debating").first()
    assert row is not None
    assert row.relevance_score == 55
