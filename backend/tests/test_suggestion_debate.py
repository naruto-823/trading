from unittest.mock import patch

import pytest

from app.services import suggestion_debate
from app.services.suggestion_debate import (
    apply_debate,
    classify_consistency,
    debate_annotation,
    downgrade_urgency,
)


def _verdict(**over):
    base = {
        "direction": "bullish", "winning_side": "bull",
        "confidence": 65, "model": "debate",
    }
    return {**base, **over}


@pytest.mark.parametrize("action, verdict, expected", [
    # 动作隐含方向与判官同向 → agree
    ("buy", _verdict(direction="bullish", winning_side="bull"), "agree"),
    ("sell", _verdict(direction="bearish", winning_side="bear"), "agree"),
    ("stop_loss", _verdict(direction="bearish", winning_side="bear"), "agree"),
    ("add", _verdict(direction="bullish", winning_side="bull"), "agree"),
    # 相反 → contradict
    ("sell", _verdict(direction="bullish", winning_side="bull"), "contradict"),
    ("buy", _verdict(direction="bearish", winning_side="bear"), "contradict"),
    # 中性 / 僵持 / 降级 → mixed
    ("buy", _verdict(direction="neutral", winning_side="bull"), "mixed"),
    ("sell", _verdict(direction="bearish", winning_side="balanced"), "mixed"),
    ("buy", _verdict(direction="bullish", winning_side="bull", model="debate-degraded"), "mixed"),
], ids=[
    "buy-bull-agree", "sell-bear-agree", "stoploss-bear-agree", "add-bull-agree",
    "sell-bull-contradict", "buy-bear-contradict",
    "neutral-mixed", "balanced-mixed", "degraded-mixed",
])
def test_classify_consistency(action, verdict, expected):
    assert classify_consistency(action, verdict) == expected


@pytest.mark.parametrize("urgency, expected", [
    ("high", "medium"),
    ("medium", "low"),
    ("low", "low"),
    ("critical", "low"),
], ids=["high-down", "medium-down", "low-stays", "unknown-fallback"])
def test_downgrade_urgency(urgency, expected):
    assert downgrade_urgency(urgency) == expected


def test_debate_annotation_agree():
    v = _verdict(winning_side="bull", confidence=70, judge_reasoning="多方证据扎实")
    ann = debate_annotation("agree", "buy", v)
    assert "辩论复核" in ann
    assert "同向" in ann
    assert "70%" in ann


def test_debate_annotation_contradict_sell_quotes_bull_case():
    # 卖建议被判看涨 → 引 bull_case
    v = _verdict(direction="bullish", bull_case="板块反弹强劲", bear_case="2x decay")
    ann = debate_annotation("contradict", "sell", v)
    assert "相左" in ann
    assert "板块反弹强劲" in ann
    assert "两可" in ann


def test_debate_annotation_contradict_buy_quotes_bear_case():
    # 买建议被判看跌 → 引 bear_case
    v = _verdict(direction="bearish", bull_case="估值低", bear_case="需求转弱")
    ann = debate_annotation("contradict", "buy", v)
    assert "需求转弱" in ann
    assert "相左" in ann
    assert "两可" in ann


def test_debate_annotation_mixed():
    v = _verdict(judge_reasoning="多空僵持")
    ann = debate_annotation("mixed", "buy", v)
    assert "存疑" in ann or "僵持" in ann


def test_apply_debate_agree_keeps_urgency():
    sug = {"action": "buy", "symbol": "GOOG.US", "urgency": "high", "thesis": "原始论点"}
    v = _verdict(direction="bullish", winning_side="bull", confidence=70)
    apply_debate(sug, v)
    assert sug["urgency"] == "high"  # agree 不降档
    assert "原始论点" in sug["thesis"]
    assert "辩论复核" in sug["thesis"]
    assert sug["debate"]["consistency"] == "agree"
    assert sug["debate"]["winning_side"] == "bull"


def test_apply_debate_contradict_downgrades_urgency():
    sug = {"action": "sell", "symbol": "INTW.US", "urgency": "high", "thesis": "卖出止损"}
    v = _verdict(direction="bullish", winning_side="bull", bull_case="正在反弹")
    apply_debate(sug, v)
    assert sug["urgency"] == "medium"  # contradict 降一档
    assert "卖出止损" in sug["thesis"]
    assert "相左" in sug["thesis"]
    assert sug["debate"]["consistency"] == "contradict"


def test_apply_debate_mixed_downgrades_urgency():
    sug = {"action": "buy", "symbol": "MSFT.US", "urgency": "medium", "thesis": "买入"}
    v = _verdict(winning_side="balanced")
    apply_debate(sug, v)
    assert sug["urgency"] == "low"
    assert sug["debate"]["consistency"] == "mixed"


def _bull_verdict():
    return _verdict(direction="bullish", winning_side="bull", confidence=70)


def test_debate_batch_applies_to_each_suggestion():
    sugs = [
        {"action": "buy", "symbol": "GOOG.US", "urgency": "high", "thesis": "t1"},
        {"action": "sell", "symbol": "INTW.US", "urgency": "high", "thesis": "t2"},
    ]
    with patch.object(suggestion_debate, "run_debate", return_value=_bull_verdict()), \
         patch.object(suggestion_debate, "build_position_context", return_value="ctx"):
        suggestion_debate.debate_batch(sugs)
    assert all("debate" in s for s in sugs)
    # GOOG buy + bullish → agree;INTW sell + bullish → contradict
    assert sugs[0]["debate"]["consistency"] == "agree"
    assert sugs[1]["debate"]["consistency"] == "contradict"


def test_debate_batch_skips_option_symbols():
    sugs = [{"action": "sell", "symbol": "MSFT260618C440000.US", "urgency": "high", "thesis": "t"}]
    with patch.object(suggestion_debate, "run_debate") as mock_run, \
         patch.object(suggestion_debate, "build_position_context", return_value="ctx"):
        suggestion_debate.debate_batch(sugs)
    mock_run.assert_not_called()
    assert "debate" not in sugs[0]


def test_debate_batch_dedups_same_symbol():
    sugs = [
        {"action": "buy", "symbol": "AAPL.US", "urgency": "high", "thesis": "t1"},
        {"action": "sell", "symbol": "AAPL.US", "urgency": "high", "thesis": "t2"},
    ]
    with patch.object(suggestion_debate, "run_debate", return_value=_bull_verdict()) as mock_run, \
         patch.object(suggestion_debate, "build_position_context", return_value="ctx"):
        suggestion_debate.debate_batch(sugs)
    assert mock_run.call_count == 1  # 同 symbol 只辩一次
    assert all("debate" in s for s in sugs)  # 但两条建议都拿到结果


def test_debate_batch_symbol_failure_isolated():
    sugs = [{"action": "buy", "symbol": "GOOG.US", "urgency": "high", "thesis": "t"}]
    with patch.object(suggestion_debate, "run_debate", side_effect=RuntimeError("boom")), \
         patch.object(suggestion_debate, "build_position_context", return_value="ctx"):
        suggestion_debate.debate_batch(sugs)  # 不抛异常
    assert "debate" not in sugs[0]  # 失败 → 该建议无标注
    assert sugs[0]["urgency"] == "high"  # urgency 不变


def test_debate_batch_disabled_noop():
    sugs = [{"action": "buy", "symbol": "GOOG.US", "urgency": "high", "thesis": "t"}]
    with patch.object(suggestion_debate.settings, "debate_enabled", False), \
         patch.object(suggestion_debate, "run_debate") as mock_run:
        suggestion_debate.debate_batch(sugs)
    mock_run.assert_not_called()
    assert "debate" not in sugs[0]
