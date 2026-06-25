"""共享新闻处理管道 process_news_item 的单元测试。

Jin10 与 Discord 共用这套:filter → event_notification 去重 → 评分 →
门控(升级辩论 / 低分跳过 / 高分推 Bark)。重点回归:
- 高 stakes 升级辩论、不直接推 Bark
- 已知 ticker(Discord 拆条带来的)优先于评分反推
- 跨源去重 hash 命中则不重复处理
"""

from unittest.mock import patch

from app.models.event_notification import EventNotification
from app.services import news_pipeline as np

_ESCALATING_TRIAGE = {
    "relevance": "direct", "score": 55, "sentiment": "positive",
    "direction": "bullish", "confidence": 60, "affected_tickers": ["MSFT"],
    "reason": "点名微软", "model": "claude-haiku-4-5-20251001",
}


def test_escalates_high_stakes_to_debate(db_session):
    with patch.object(np, "SessionLocal", return_value=db_session), \
         patch.object(np, "score_relevance", return_value=dict(_ESCALATING_TRIAGE)), \
         patch.object(np, "submit_debate") as mock_submit, \
         patch.object(np, "send_bark") as mock_bark:
        result = np.process_news_item(
            "微软发布 AI 新品,股价异动",
            source="discord",
            dedup_seed="msft-ai-001",
            is_important=True,
            source_label="Discord",
        )

    assert result == "escalated"
    mock_bark.assert_not_called()
    mock_submit.assert_called_once()
    row = db_session.query(EventNotification).filter_by(push_status="debating").first()
    assert row is not None
    assert row.relevance_score == 55


def test_known_ticker_overrides_scored_affected(db_session):
    # Discord 拆条已知是 GOOGL;即便评分反推成别的,也以已知为准
    triage = dict(_ESCALATING_TRIAGE)
    triage["affected_tickers"] = ["MSFT"]
    with patch.object(np, "SessionLocal", return_value=db_session), \
         patch.object(np, "score_relevance", return_value=triage), \
         patch.object(np, "submit_debate"), \
         patch.object(np, "send_bark"):
        np.process_news_item(
            "谷歌 Gemini 3.5 发布延期",
            source="discord",
            dedup_seed="googl-gemini-001",
            is_important=True,
            known_tickers=["GOOGL"],
            source_label="Discord",
        )

    row = db_session.query(EventNotification).filter_by(push_status="debating").first()
    assert row.symbol == "GOOGL"
