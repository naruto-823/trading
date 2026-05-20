from app.config import Settings


def test_settings_loads():
    s = Settings()
    assert s.relevance_threshold >= 0


def test_debate_settings_defaults():
    s = Settings()
    assert s.debate_enabled is True
    assert s.debate_bull_model == "claude-haiku-4-5-20251001"
    assert s.debate_bear_model == "claude-haiku-4-5-20251001"
    assert s.debate_judge_model == "claude-sonnet-4-6"
    assert s.debate_escalate_score_lo == 35
    assert s.debate_escalate_score_hi == 65
    assert s.debate_escalate_min_importance == 5
    assert s.debate_timeout_seconds == 90
    assert s.debate_zombie_minutes == 5
    assert s.debate_max_workers == 2
    assert s.debate_websearch_enabled is True
    assert s.debate_websearch_max_uses == 3
    assert s.debate_daily_cap == 0
