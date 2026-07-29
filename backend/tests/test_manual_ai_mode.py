from unittest.mock import patch

from app.config import settings
from app.services.briefing import build_briefing
from app.services.relevance_scorer import score_relevance


def test_rule_feed_does_not_call_model_when_ai_is_disallowed():
    with patch("app.services.relevance_scorer.Anthropic") as anthropic:
        result = score_relevance("美联储公布最新利率决定", allow_ai=False)

    anthropic.assert_not_called()
    assert result["model"] == "fail-open"
    assert "自动 AI 已关闭" in result["reason"]


def test_dashboard_load_does_not_generate_briefing_in_manual_mode(monkeypatch, db_session):
    monkeypatch.setattr(settings, "automatic_ai_enabled", False)
    with patch("app.services.briefing._call_llm_anthropic") as call_model:
        result = build_briefing(db_session, force_refresh=False)

    call_model.assert_not_called()
    assert "自动 AI 已关闭" in result["market_summary"] or result["stocks"] == []
