import json
from unittest.mock import MagicMock, patch

from app.services import debate_scorer


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


def _fake_resp(payload: dict):
    resp = MagicMock()
    resp.content = [_Block(json.dumps(payload, ensure_ascii=False))]
    return resp


def test_run_advocate_parses_json():
    payload = {
        "stance_score": 80,
        "key_points": ["反弹 13%", "贸易缓和"],
        "strongest_argument": "板块 beta 强",
        "risks_to_own_view": "2x ETF 有 decay",
    }
    with patch.object(debate_scorer, "Anthropic") as mock_cls, \
         patch.object(debate_scorer.settings, "anthropic_api_key", "k"):
        mock_cls.return_value.messages.create.return_value = _fake_resp(payload)
        out = debate_scorer._run_advocate("bull", "model-x", "英特尔大涨", "持仓:INTW", "简报")
    assert out["side"] == "bull"
    assert out["stance_score"] == 80
    assert out["key_points"] == ["反弹 13%", "贸易缓和"]
    assert out["strongest_argument"] == "板块 beta 强"


def test_run_advocate_fail_returns_none():
    with patch.object(debate_scorer, "Anthropic") as mock_cls, \
         patch.object(debate_scorer.settings, "anthropic_api_key", "k"):
        mock_cls.return_value.messages.create.side_effect = RuntimeError("boom")
        assert debate_scorer._run_advocate("bear", "model-x", "x", "ctx", "") is None
