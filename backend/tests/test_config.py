from app.config import Settings


def test_settings_loads():
    s = Settings()
    assert s.relevance_threshold >= 0
