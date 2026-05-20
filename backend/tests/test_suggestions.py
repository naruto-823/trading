from datetime import datetime
from unittest.mock import patch

from app.models.suggestion import Suggestion
from app.services import suggestions as sug_service


def _seed_batch(db):
    row = Suggestion(
        row_id="seed1", batch_id="seedbatch", generated_at=datetime.utcnow(),
        summary="种子批次", suggestion_key="GOOG.US-buy", action="buy",
        symbol="GOOG.US", urgency="medium", thesis="种子建议",
    )
    db.add(row)
    db.commit()


def test_build_suggestions_no_refresh_returns_latest_without_regen(db_session):
    _seed_batch(db_session)
    with patch.object(sug_service, "list_positions", return_value=[object()]), \
         patch.object(sug_service, "get_latest_account", return_value=object()), \
         patch.object(sug_service, "_call_opus") as mock_opus:
        resp = sug_service.build_suggestions(db_session, force_refresh=False)
    mock_opus.assert_not_called()
    assert resp["cache_hit"] is True
    assert resp["summary"] == "种子批次"


def test_build_suggestions_no_refresh_no_batch_returns_empty(db_session):
    with patch.object(sug_service, "list_positions", return_value=[object()]), \
         patch.object(sug_service, "get_latest_account", return_value=object()), \
         patch.object(sug_service, "_call_opus") as mock_opus:
        resp = sug_service.build_suggestions(db_session, force_refresh=False)
    mock_opus.assert_not_called()
    assert resp["suggestions"] == []
    assert "尚未生成" in resp["summary"]
