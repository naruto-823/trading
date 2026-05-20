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


_TRIAGE = {
    "relevance": "direct", "score": 55, "sentiment": "neutral",
    "direction": "neutral", "confidence": 50, "affected_tickers": ["INTW"],
    "reason": "点名持仓", "model": "claude-haiku-4-5-20251001",
}

_JUDGE_PAYLOAD = {
    "relevance": "direct", "score": 72, "sentiment": "positive",
    "direction": "bullish", "confidence": 65, "affected_tickers": ["INTW"],
    "reason": "板块反弹但 2x ETF 需止盈纪律",
    "bull_case": "贸易缓和 + 板块 beta", "bear_case": "2x ETF decay",
    "judge_reasoning": "多方证据更扎实", "winning_side": "bull",
}


def test_run_debate_happy_path():
    advocate_payload = {
        "stance_score": 70, "key_points": ["p1"],
        "strongest_argument": "arg", "risks_to_own_view": "risk",
    }

    def _route(*args, **kwargs):
        # 看多/看空并行跑,调用顺序不定 —— 按 system prompt 路由,不依赖顺序
        # 判官 prompt 含"判官",辩手 prompt 含"辩手"
        if "判官" in kwargs.get("system", ""):
            return _fake_resp(_JUDGE_PAYLOAD)
        return _fake_resp(advocate_payload)

    with patch.object(debate_scorer, "Anthropic") as mock_cls, \
         patch.object(debate_scorer, "gather_research", return_value="简报"), \
         patch.object(debate_scorer.settings, "anthropic_api_key", "k"):
        mock_cls.return_value.messages.create.side_effect = _route
        verdict = debate_scorer.run_debate("英特尔大涨", _TRIAGE, "持仓:INTW")
    assert verdict["score"] == 72
    assert verdict["direction"] == "bullish"
    assert verdict["winning_side"] == "bull"
    assert verdict["model"] == "debate"
    # verdict 必须含现有 scorer 的全部字段
    for key in ("relevance", "sentiment", "confidence", "affected_tickers", "reason"):
        assert key in verdict


def test_run_debate_judge_fails_falls_back_to_triage():
    advocate_payload = {
        "stance_score": 70, "key_points": [],
        "strongest_argument": "", "risks_to_own_view": "",
    }

    def _route(*args, **kwargs):
        if "判官" in kwargs.get("system", ""):
            raise RuntimeError("judge boom")
        return _fake_resp(advocate_payload)

    with patch.object(debate_scorer, "Anthropic") as mock_cls, \
         patch.object(debate_scorer, "gather_research", return_value=""), \
         patch.object(debate_scorer.settings, "anthropic_api_key", "k"):
        mock_cls.return_value.messages.create.side_effect = _route
        verdict = debate_scorer.run_debate("x", _TRIAGE, "ctx")
    # 回退 triage:score/direction 来自 triage,model 标降级
    assert verdict["score"] == 55
    assert verdict["model"] == "debate-degraded"
    assert "降级" in verdict["reason"]


def test_run_debate_both_advocates_fail_falls_back():
    with patch.object(debate_scorer, "Anthropic") as mock_cls, \
         patch.object(debate_scorer, "gather_research", return_value=""), \
         patch.object(debate_scorer.settings, "anthropic_api_key", "k"):
        mock_cls.return_value.messages.create.side_effect = RuntimeError("boom")
        verdict = debate_scorer.run_debate("x", _TRIAGE, "ctx")
    assert verdict["model"] == "debate-degraded"
    assert verdict["score"] == 55


def test_run_debate_judge_float_string_score_no_crash():
    """判官返回字符串型 float 分数,run_debate 不能抛异常,且能正确归一化。"""
    advocate_payload = {
        "stance_score": 70, "key_points": [],
        "strongest_argument": "", "risks_to_own_view": "",
    }
    judge_bad = {**_JUDGE_PAYLOAD, "score": "72.5", "confidence": "60.0"}

    def _route(*args, **kwargs):
        if "判官" in kwargs.get("system", ""):
            return _fake_resp(judge_bad)
        return _fake_resp(advocate_payload)

    with patch.object(debate_scorer, "Anthropic") as mock_cls, \
         patch.object(debate_scorer, "gather_research", return_value=""), \
         patch.object(debate_scorer.settings, "anthropic_api_key", "k"):
        mock_cls.return_value.messages.create.side_effect = _route
        verdict = debate_scorer.run_debate("x", _TRIAGE, "ctx")
    assert verdict["score"] == 72
    assert verdict["confidence"] == 60
    assert verdict["model"] == "debate"


def test_run_debate_one_advocate_fails_still_judges():
    """一方辩手挂了,判官用存活的一方继续裁,仍出 debate verdict。"""
    advocate_payload = {
        "stance_score": 70, "key_points": ["p"],
        "strongest_argument": "a", "risks_to_own_view": "r",
    }

    def _route(*args, **kwargs):
        system = kwargs.get("system", "")
        if "判官" in system:
            return _fake_resp(_JUDGE_PAYLOAD)
        if "看空辩手" in system:
            raise RuntimeError("bear down")
        return _fake_resp(advocate_payload)

    with patch.object(debate_scorer, "Anthropic") as mock_cls, \
         patch.object(debate_scorer, "gather_research", return_value=""), \
         patch.object(debate_scorer.settings, "anthropic_api_key", "k"):
        mock_cls.return_value.messages.create.side_effect = _route
        verdict = debate_scorer.run_debate("x", _TRIAGE, "ctx")
    assert verdict["model"] == "debate"
    assert verdict["score"] == 72


from datetime import datetime

from app.models.position import Position


def _pos(symbol, name, qty, cost, cur, mv, pnl, ratio):
    return Position(
        synced_at=datetime.utcnow(), symbol=symbol, market="US", name=name,
        quantity=qty, available_qty=qty, cost_price=cost, current_price=cur,
        market_value=mv, unrealized_pnl=pnl, unrealized_pnl_ratio=ratio,
        currency="USD",
    )


def test_build_position_context_includes_affected_detail(db_session):
    db_session.add(_pos("MSFT", "Microsoft", 90, 417.2, 415.4, 37386, -163, -0.004))
    db_session.add(_pos("INTW", "GraniteShares 2x INTC", 16, 361.0, 302.0, 4832, -944, -0.163))
    db_session.commit()

    with patch.object(debate_scorer, "SessionLocal", return_value=db_session):
        ctx = debate_scorer.build_position_context(["INTW"])

    # 总览含全部重仓
    assert "MSFT" in ctx and "INTW" in ctx
    # 受影响标的带成本/盈亏明细
    assert "361" in ctx  # INTW 成本
    assert "-16" in ctx  # INTW 盈亏%


def test_build_position_context_no_positions(db_session):
    with patch.object(debate_scorer, "SessionLocal", return_value=db_session):
        ctx = debate_scorer.build_position_context([])
    assert "无持仓" in ctx
