from unittest.mock import MagicMock, patch

from app.services import debate_research


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


def _fake_resp(text):
    resp = MagicMock()
    resp.content = [_Block(text)]
    return resp


def test_gather_research_returns_brief():
    with patch.object(debate_research, "Anthropic") as mock_cls, \
         patch.object(debate_research.settings, "anthropic_api_key", "k"), \
         patch.object(debate_research.settings, "debate_websearch_enabled", True):
        mock_cls.return_value.messages.create.return_value = _fake_resp("INTC 近期反弹 13%")
        brief = debate_research.gather_research("英特尔大涨", ["INTC"])
    assert "INTC" in brief


def test_gather_research_fail_soft_returns_empty():
    with patch.object(debate_research, "Anthropic") as mock_cls, \
         patch.object(debate_research.settings, "anthropic_api_key", "k"), \
         patch.object(debate_research.settings, "debate_websearch_enabled", True):
        mock_cls.return_value.messages.create.side_effect = RuntimeError("boom")
        brief = debate_research.gather_research("英特尔大涨", ["INTC"])
    assert brief == ""


def test_gather_research_disabled_returns_empty():
    with patch.object(debate_research.settings, "debate_websearch_enabled", False):
        assert debate_research.gather_research("x", ["INTC"]) == ""
