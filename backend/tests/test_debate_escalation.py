import pytest

from app.services.debate_scorer import should_escalate

# triage 基础模板(单 Haiku 的正常输出)
BASE = {
    "relevance": "indirect", "score": 20, "sentiment": "neutral",
    "direction": "neutral", "confidence": 50, "affected_tickers": [],
    "reason": "", "model": "claude-haiku-4-5-20251001",
}


def _triage(**over):
    return {**BASE, **over}


@pytest.mark.parametrize("triage, importance, expected", [
    # 点名持仓 → 升级
    (_triage(affected_tickers=["MSFT"]), 3, True),
    # 高 importance 宏观(无 ticker)→ 升级
    (_triage(score=10), 5, True),
    # 分数落临界带 → 升级
    (_triage(score=50), 3, True),
    (_triage(score=35), 3, True),
    (_triage(score=65), 3, True),
    # 低分噪声 → 不升级
    (_triage(score=20), 3, False),
    # 高分但不涉持仓、importance 不够 → 不升级(走快路)
    (_triage(score=90), 3, False),
    # triage 自己 fail-open → 不升级
    ({**_triage(score=100), "model": "fail-open"}, 5, False),
])
def test_should_escalate(triage, importance, expected):
    assert should_escalate(triage, importance) is expected
