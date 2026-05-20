import pytest

from app.services.suggestion_debate import classify_consistency


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
