# 辩论评分引擎 Phase 2 实施计划 —— 建议引擎接入辩论复核

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `build_suggestions` 的 Opus 批量产出之后,加一道看多/看空辩论的对抗复核 —— verdict 与建议矛盾时标注 + 降 urgency(不删不改动作),并由定时 worker 每天 2 次后台预生成。

**Architecture:** 新增 `suggestion_debate.py`(in-place 后处理,跟 `_verify_prices`/`_check_affordability` 同模式),对每个唯一非期权候选 symbol 跑 Phase 1 的 `run_debate`,verdict→consistency 分类→调整 urgency/thesis/debate。新增 `suggestions_worker.py` 定时跑 `build_suggestions(force_refresh=True)`;按需 API 改成只返回 worker 产出的最新批次、不内联重算。

**Tech Stack:** Python 3.11 / SQLAlchemy 2.0 / SQLite / APScheduler `CronTrigger` / `concurrent.futures.ThreadPoolExecutor` / pytest。

**Spec:** `docs/superpowers/specs/2026-05-20-debate-scorer-phase2-design.md`

**前置:** Phase 1 已实现 —— `app/services/debate_scorer.py` 的 `run_debate(content, triage, position_ctx) -> dict` 与 `build_position_context(affected_tickers) -> str` 已建好。`run_debate` 永远返回 verdict dict(fail-open),含键 `relevance/score/sentiment/direction/confidence/affected_tickers/reason/bull_case/bear_case/judge_reasoning/winning_side/model`。

---

## 文件结构

**新建:**
- `backend/app/services/suggestion_debate.py` — `debate_batch` + `classify_consistency` / `downgrade_urgency` / `debate_annotation` / `apply_debate` / `_debate_one` / `_synth_inputs` / `_is_option`
- `backend/app/workers/suggestions_worker.py` — 定时 worker
- `backend/tests/test_suggestion_debate.py`
- `backend/tests/test_suggestion_model.py`
- `backend/tests/test_suggestions.py`
- `backend/tests/test_suggestions_worker.py`

**修改:**
- `backend/app/models/suggestion.py` — 加 `debate_json` 列
- `backend/app/db.py` — `_apply_lightweight_migrations` 加 `debate_json`
- `backend/app/services/suggestions.py` — 调 `debate_batch`、改 `force_refresh=False` 行为、`_persist_batch` / `_row_to_dict` 加 debate、删 `CACHE_TTL_SECONDS`
- `backend/app/workers/scheduler.py` — 注册 suggestions_worker

**约定:** `pytest` / `ruff` 命令在 `backend/` 目录下用 `.venv/bin/python -m ...` 执行;`git` 命令在仓库根 `/Users/naruo/Desktop/work/ai/trading` 执行;提交到 `main`;每条 commit message 末尾空一行加 `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`。

---

## Task 1: Schema — suggestion.debate_json 列

**Files:**
- Modify: `backend/app/models/suggestion.py`(在 `affordability_json` 之后)
- Modify: `backend/app/db.py`(`_apply_lightweight_migrations` 的 `added` 列表)
- Create: `backend/tests/test_suggestion_model.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_suggestion_model.py`:

```python
from datetime import datetime

from app.models.suggestion import Suggestion


def test_suggestion_debate_json_roundtrips(db_session):
    row = Suggestion(
        row_id="r1",
        batch_id="b1",
        generated_at=datetime.utcnow(),
        suggestion_key="INTW.US-sell",
        action="sell",
        symbol="INTW.US",
        debate_json='{"consistency": "contradict"}',
    )
    db_session.add(row)
    db_session.commit()

    got = db_session.query(Suggestion).filter_by(row_id="r1").first()
    assert got.debate_json == '{"consistency": "contradict"}'
```

- [ ] **Step 2: 跑测试验证失败**

Run:`.venv/bin/python -m pytest tests/test_suggestion_model.py -v`
Expected: FAIL,`TypeError: 'debate_json' is an invalid keyword argument for Suggestion`

- [ ] **Step 3: 模型加列**

在 `backend/app/models/suggestion.py` 的 `affordability_json` 那一行之后插入:

```python

    # 辩论复核结果(Phase 2):{direction, winning_side, confidence, consistency,
    # bull_case, bear_case, judge_reasoning}
    debate_json: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 4: 轻量迁移加列**

在 `backend/app/db.py` 的 `_apply_lightweight_migrations` 的 `added` 列表末尾(`("event_notification", "debate_json", "TEXT")` 之后)追加:

```python
        ("suggestion", "debate_json", "TEXT"),
```

- [ ] **Step 5: 跑测试验证通过**

Run:`.venv/bin/python -m pytest tests/test_suggestion_model.py -v`
Expected: PASS,1 passed。

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/suggestion.py backend/app/db.py backend/tests/test_suggestion_model.py
git commit -m "feat(debate): suggestion 加 debate_json 列"
```
(commit message 末尾追加 Co-Authored-By 行。)

---

## Task 2: classify_consistency — 一致性分类

**Files:**
- Create: `backend/app/services/suggestion_debate.py`
- Create: `backend/tests/test_suggestion_debate.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_suggestion_debate.py`:

```python
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
```

- [ ] **Step 2: 跑测试验证失败**

Run:`.venv/bin/python -m pytest tests/test_suggestion_debate.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'app.services.suggestion_debate'`

- [ ] **Step 3: 写 suggestion_debate.py 骨架 + classify_consistency**

创建 `backend/app/services/suggestion_debate.py`:

```python
"""建议引擎的辩论复核(Phase 2)

对 Opus 批量产出的建议做看多/看空辩论二次复核 —— 跑 Phase 1 的 run_debate 拿独立
第二意见,verdict 与建议动作矛盾时标注 + 降 urgency(不删不改动作)。
in-place 后处理,跟 suggestions._verify_prices / _check_affordability 同模式。

spec: docs/superpowers/specs/2026-05-20-debate-scorer-phase2-design.md
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 动作 → 隐含方向
_BULLISH_ACTIONS = ("buy", "add")
_BEARISH_ACTIONS = ("sell", "stop_loss")


def classify_consistency(action: str, verdict: dict) -> str:
    """建议动作 vs 辩论 verdict → "agree" / "contradict" / "mixed"。"""
    if verdict.get("model") == "debate-degraded":
        return "mixed"
    if verdict.get("winning_side") == "balanced":
        return "mixed"
    direction = verdict.get("direction")
    if direction not in ("bullish", "bearish"):
        return "mixed"
    if action in _BULLISH_ACTIONS:
        implied = "bullish"
    elif action in _BEARISH_ACTIONS:
        implied = "bearish"
    else:
        return "mixed"  # 未知动作
    return "agree" if direction == implied else "contradict"
```

- [ ] **Step 4: 跑测试验证通过**

Run:`.venv/bin/python -m pytest tests/test_suggestion_debate.py -v`
Expected: PASS,9 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/suggestion_debate.py backend/tests/test_suggestion_debate.py
git commit -m "feat(debate): classify_consistency — 建议动作 vs 辩论一致性分类"
```
(append Co-Authored-By trailer.)

---

## Task 3: urgency 降档 + thesis 标注

**Files:**
- Modify: `backend/app/services/suggestion_debate.py`
- Modify: `backend/tests/test_suggestion_debate.py`

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_suggestion_debate.py` 末尾追加:

```python
from app.services.suggestion_debate import debate_annotation, downgrade_urgency


@pytest.mark.parametrize("urgency, expected", [
    ("high", "medium"),
    ("medium", "low"),
    ("low", "low"),
], ids=["high-down", "medium-down", "low-stays"])
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


def test_debate_annotation_mixed():
    v = _verdict(judge_reasoning="多空僵持")
    ann = debate_annotation("mixed", "buy", v)
    assert "存疑" in ann or "僵持" in ann
```

- [ ] **Step 2: 跑测试验证失败**

Run:`.venv/bin/python -m pytest tests/test_suggestion_debate.py -k "downgrade or annotation" -v`
Expected: FAIL,`ImportError: cannot import name 'debate_annotation'`

- [ ] **Step 3: 加 downgrade_urgency + debate_annotation**

在 `backend/app/services/suggestion_debate.py` 的 `classify_consistency` 之后追加:

```python
_URGENCY_DOWNGRADE = {"high": "medium", "medium": "low", "low": "low"}


def downgrade_urgency(urgency: str) -> str:
    """urgency 降一档(high→medium→low,low 保持)。未知值兜底为 low。"""
    return _URGENCY_DOWNGRADE.get(urgency, "low")


_DIR_CN = {"bullish": "涨", "bearish": "跌", "neutral": "平"}


def debate_annotation(consistency: str, action: str, verdict: dict) -> str:
    """生成追加到 thesis 末尾的辩论复核行。"""
    conf = verdict.get("confidence", 0)
    judge = (verdict.get("judge_reasoning") or "")[:120]
    if consistency == "agree":
        side = verdict.get("winning_side", "")
        return f"⚖️ 辩论复核:判官同向({side},判官 {conf}%)— {judge}"
    if consistency == "contradict":
        dir_cn = _DIR_CN.get(verdict.get("direction"), "平")
        # 引与建议动作相反那一方的 case:卖建议被判看涨→bull_case;买建议被判看跌→bear_case
        if action in _BEARISH_ACTIONS:
            opp_case = verdict.get("bull_case") or ""
        else:
            opp_case = verdict.get("bear_case") or ""
        return (
            f"⚖️ 辩论复核:判官倾向看{dir_cn},与本动作相左 —— "
            f"{opp_case[:120]}。两可,你来定。"
        )
    # mixed
    return f"⚖️ 辩论复核:多空僵持/存疑 — {judge}"
```

- [ ] **Step 4: 跑测试验证通过**

Run:`.venv/bin/python -m pytest tests/test_suggestion_debate.py -v`
Expected: PASS,16 passed(9 + 3 + 4)。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/suggestion_debate.py backend/tests/test_suggestion_debate.py
git commit -m "feat(debate): downgrade_urgency + debate_annotation"
```
(append Co-Authored-By trailer.)

---

## Task 4: apply_debate — verdict 应用到单条建议

**Files:**
- Modify: `backend/app/services/suggestion_debate.py`
- Modify: `backend/tests/test_suggestion_debate.py`

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_suggestion_debate.py` 末尾追加:

```python
from app.services.suggestion_debate import apply_debate


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
```

- [ ] **Step 2: 跑测试验证失败**

Run:`.venv/bin/python -m pytest tests/test_suggestion_debate.py -k apply_debate -v`
Expected: FAIL,`ImportError: cannot import name 'apply_debate'`

- [ ] **Step 3: 加 apply_debate**

在 `backend/app/services/suggestion_debate.py` 的 `debate_annotation` 之后追加:

```python
def apply_debate(suggestion: dict, verdict: dict) -> None:
    """把一个 verdict 应用到一条建议(in-place):
    分类一致性 → contradict/mixed 时降 urgency → 追加 thesis 行 → 写 debate 字段。
    """
    action = suggestion.get("action", "")
    consistency = classify_consistency(action, verdict)
    if consistency in ("contradict", "mixed"):
        suggestion["urgency"] = downgrade_urgency(suggestion.get("urgency", "medium"))
    annotation = debate_annotation(consistency, action, verdict)
    base_thesis = suggestion.get("thesis", "")
    suggestion["thesis"] = f"{base_thesis}\n{annotation}" if base_thesis else annotation
    suggestion["debate"] = {
        "direction": verdict.get("direction", "neutral"),
        "winning_side": verdict.get("winning_side", "balanced"),
        "confidence": verdict.get("confidence", 0),
        "consistency": consistency,
        "bull_case": verdict.get("bull_case", ""),
        "bear_case": verdict.get("bear_case", ""),
        "judge_reasoning": verdict.get("judge_reasoning", ""),
    }
```

- [ ] **Step 4: 跑测试验证通过**

Run:`.venv/bin/python -m pytest tests/test_suggestion_debate.py -v`
Expected: PASS,19 passed(16 + 3)。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/suggestion_debate.py backend/tests/test_suggestion_debate.py
git commit -m "feat(debate): apply_debate — verdict 应用到单条建议"
```
(append Co-Authored-By trailer.)

---

## Task 5: debate_batch — 批次编排

**Files:**
- Modify: `backend/app/services/suggestion_debate.py`
- Modify: `backend/tests/test_suggestion_debate.py`

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_suggestion_debate.py` 末尾追加:

```python
from unittest.mock import patch

from app.services import suggestion_debate


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
```

- [ ] **Step 2: 跑测试验证失败**

Run:`.venv/bin/python -m pytest tests/test_suggestion_debate.py -k debate_batch -v`
Expected: FAIL,`AttributeError: module 'app.services.suggestion_debate' has no attribute 'debate_batch'`

- [ ] **Step 3: 加 imports + _is_option / _synth_inputs / _debate_one / debate_batch**

在 `backend/app/services/suggestion_debate.py` 顶部 import 区(`import logging` 之后、`logger = ...` 之前)追加:

```python
from concurrent.futures import ThreadPoolExecutor

from app.config import settings
from app.services.debate_scorer import build_position_context, run_debate
```

然后在 `apply_debate` 之后追加:

```python
def _is_option(symbol: str) -> bool:
    """期权合约 symbol 识别(如 MSFT260618C440000.US)。

    本地副本 —— 与 suggestions._is_option 同口径,独立定义以避免 suggestions ↔
    suggestion_debate 的循环 import。
    """
    return (
        len(symbol) > 12
        and any(c in symbol for c in ("C", "P"))
        and any(d.isdigit() for d in symbol)
    )


def _synth_inputs(symbol: str) -> tuple[str, dict]:
    """合成喂给 run_debate 的中性 content + triage(不喂建议动作,要独立第二意见)。"""
    content = (
        f"复核:此刻应该看多还是看空 {symbol}?"
        f"结合该标的近况与用户持仓给方向判断。"
    )
    triage = {
        "relevance": "direct", "score": 60, "sentiment": "neutral",
        "direction": "neutral", "confidence": 50,
        "affected_tickers": [symbol], "reason": "建议复核", "model": "suggestion",
    }
    return content, triage


def _debate_one(symbol: str) -> dict | None:
    """跑一个 symbol 的辩论复核。失败返回 None。"""
    try:
        content, triage = _synth_inputs(symbol)
        position_ctx = build_position_context([symbol])
        return run_debate(content, triage, position_ctx)
    except Exception as exc:
        logger.warning("suggestion debate failed for %s: %s", symbol, exc)
        return None


def debate_batch(suggestions: list[dict]) -> None:
    """对建议批次做辩论复核 —— in-place 改每条建议的 urgency/thesis/debate。

    debate_enabled=False → no-op。期权合约 symbol 跳过。同 symbol 只辩一次。
    单 symbol 失败被隔离,不影响其他建议。
    """
    if not settings.debate_enabled:
        return
    symbols = {
        s["symbol"]
        for s in suggestions
        if s.get("symbol") and not _is_option(s["symbol"])
    }
    if not symbols:
        return
    verdicts: dict[str, dict | None] = {}
    with ThreadPoolExecutor(
        max_workers=max(1, settings.debate_max_workers), thread_name_prefix="sug-debate"
    ) as ex:
        futures = {sym: ex.submit(_debate_one, sym) for sym in symbols}
        for sym, fut in futures.items():
            try:
                verdicts[sym] = fut.result()
            except Exception as exc:
                logger.warning("suggestion debate future failed for %s: %s", sym, exc)
                verdicts[sym] = None
    for s in suggestions:
        verdict = verdicts.get(s.get("symbol", ""))
        if verdict is not None:
            apply_debate(s, verdict)
```

- [ ] **Step 4: 跑测试验证通过**

Run:`.venv/bin/python -m pytest tests/test_suggestion_debate.py -v`
Expected: PASS,24 passed(19 + 5)。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/suggestion_debate.py backend/tests/test_suggestion_debate.py
git commit -m "feat(debate): debate_batch — 建议批次辩论复核编排"
```
(append Co-Authored-By trailer.)

---

## Task 6: 持久化 / 序列化 debate

**Files:**
- Modify: `backend/app/services/suggestions.py`(`_persist_batch`、`_row_to_dict`)
- Modify: `backend/tests/test_suggestion_model.py`

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_suggestion_model.py` 末尾追加:

```python
from app.services import suggestions as sug_service


def test_persist_and_serialize_debate(db_session):
    suggestions = [{
        "action": "sell", "symbol": "INTW.US", "qty": "16", "price": "约 302",
        "urgency": "medium", "thesis": "卖出\n⚖️ 辩论复核:…",
        "data_points": ["dp1"],
        "debate": {"consistency": "contradict", "direction": "bullish",
                   "winning_side": "bull", "confidence": 65,
                   "bull_case": "反弹", "bear_case": "", "judge_reasoning": "多方更扎实"},
    }]
    rows = sug_service._persist_batch(
        db_session, "batch1", datetime.utcnow(), "summary", suggestions
    )
    assert rows[0].debate_json is not None

    d = sug_service._row_to_dict(rows[0])
    assert d["debate"]["consistency"] == "contradict"
    assert d["debate"]["winning_side"] == "bull"
```

- [ ] **Step 2: 跑测试验证失败**

Run:`.venv/bin/python -m pytest tests/test_suggestion_model.py::test_persist_and_serialize_debate -v`
Expected: FAIL,`KeyError: 'debate'`(`_row_to_dict` 还没产出 debate 字段)

- [ ] **Step 3: _persist_batch 写入 + _row_to_dict 读出**

在 `backend/app/services/suggestions.py` 的 `_persist_batch` 里,`SuggestionRow(...)` 构造里
`affordability_json=(...)` 那一项之后追加一行:

```python
            debate_json=(
                json.dumps(s["debate"], ensure_ascii=False) if s.get("debate") else None
            ),
```

在 `_row_to_dict` 返回的 dict 里,`"affordability": ...` 那一行之后追加:

```python
        "debate": json.loads(row.debate_json) if row.debate_json else None,
```

- [ ] **Step 4: 跑测试验证通过**

Run:`.venv/bin/python -m pytest tests/test_suggestion_model.py -v`
Expected: PASS,2 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/suggestions.py backend/tests/test_suggestion_model.py
git commit -m "feat(debate): suggestion 批次持久化/序列化 debate 字段"
```
(append Co-Authored-By trailer.)

---

## Task 7: 接入 build_suggestions

**Files:**
- Modify: `backend/app/services/suggestions.py`
- Create: `backend/tests/test_suggestions.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_suggestions.py`:

```python
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
```

- [ ] **Step 2: 跑测试验证失败**

Run:`.venv/bin/python -m pytest tests/test_suggestions.py -v`
Expected: FAIL —— `test_build_suggestions_no_refresh_no_batch_returns_empty` 失败:当前
`force_refresh=False` 无批次时会 fall through 去重算(调到 `_call_opus`),`mock_opus`
被调用 / 或 summary 不含「尚未生成」。

- [ ] **Step 3: 改 build_suggestions —— force_refresh=False 只读最新批次**

在 `backend/app/services/suggestions.py`:

(a) import 区追加(`from app.services.positions import list_positions` 之后):

```python
from app.services import suggestion_debate
```

(b) 把 `force_refresh` 的缓存块整段替换。原代码:

```python
    # 优先复用 DB 里的最新批次（cache_hit）
    if not force_refresh:
        latest_batch = _load_latest_batch(db)
        if latest_batch is not None:
            generated_at, rows = latest_batch
            age = datetime.now(timezone.utc) - _ensure_utc(generated_at)
            if age < timedelta(seconds=CACHE_TTL_SECONDS):
                return _batch_to_response(rows, cache_hit=True)
```

替换为:

```python
    # 按需读取:只返回 worker 产出的最新批次,绝不内联重算(Phase 2)。
    # freshness 由 suggestions_worker 负责;force_refresh=True 才完整重算。
    if not force_refresh:
        latest_batch = _load_latest_batch(db)
        if latest_batch is None:
            return _empty_response("建议尚未生成,等下次定时刷新或手动刷新")
        _, rows = latest_batch
        return _batch_to_response(rows, cache_hit=True)
```

(c) 删除文件顶部的 `CACHE_TTL_SECONDS` 常量定义(`CACHE_TTL_SECONDS = 30 * 60 ...` 那一行)。

(d) 在 `build_suggestions` 里 `_check_affordability(...)` 调用之后、`# 持久化这一批` 注释之前,插入:

```python
    # 辩论复核(Phase 2):对每条建议跑看多/看空辩论,矛盾→标注+降 urgency。
    # 包 try/except —— 辩论失败也不能丢 Opus 建议。
    try:
        suggestion_debate.debate_batch(result.get("suggestions", []))
    except Exception as exc:
        logger.warning("debate_batch 失败,落库未复核批次: %s", exc)
```

- [ ] **Step 4: 跑测试验证通过**

Run:`.venv/bin/python -m pytest tests/test_suggestions.py -v`
Expected: PASS,2 passed。

- [ ] **Step 5: 跑全量测试确认无回归**

Run:`.venv/bin/python -m pytest tests/ -v`
Expected: 全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/suggestions.py backend/tests/test_suggestions.py
git commit -m "feat(debate): build_suggestions 接入 debate_batch + 按需只读最新批次"
```
(append Co-Authored-By trailer.)

---

## Task 8: suggestions_worker

**Files:**
- Create: `backend/app/workers/suggestions_worker.py`
- Create: `backend/tests/test_suggestions_worker.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_suggestions_worker.py`:

```python
from unittest.mock import patch

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.workers import suggestions_worker


def test_register_adds_job():
    sched = AsyncIOScheduler()
    suggestions_worker.register(sched)
    job = sched.get_job("suggestions-refresh")
    assert job is not None
    assert job.name


async def test_run_suggestions_job_swallows_success(monkeypatch):
    monkeypatch.setattr(
        suggestions_worker, "_run_once_sync", lambda: {"suggestions": [1, 2, 3]}
    )
    # 不抛异常即通过
    await suggestions_worker.run_suggestions_job()
```

- [ ] **Step 2: 跑测试验证失败**

Run:`.venv/bin/python -m pytest tests/test_suggestions_worker.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'app.workers.suggestions_worker'`

- [ ] **Step 3: 写 suggestions_worker.py**

创建 `backend/app/workers/suggestions_worker.py`:

```python
"""建议批次定时预生成 worker —— 每天 2 次(美股盘前 13:00 UTC / 收盘后 22:00 UTC)

build_suggestions(force_refresh=True) 含辩论复核,耗时数分钟,放后台跑;
用户按需打开建议页永远命中 worker 产出的最新批次,不内联等待。

spec: docs/superpowers/specs/2026-05-20-debate-scorer-phase2-design.md
"""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi.concurrency import run_in_threadpool

from app.db import SessionLocal
from app.services.suggestions import build_suggestions
from app.workers.scheduler import record_duration

logger = logging.getLogger(__name__)

JOB_ID = "suggestions-refresh"


def _run_once_sync() -> dict:
    db = SessionLocal()
    try:
        return build_suggestions(db, force_refresh=True)
    finally:
        db.close()


async def run_suggestions_job() -> None:
    t0 = time.time()
    try:
        result = await run_in_threadpool(_run_once_sync)
        logger.info("suggestions-refresh: 生成 %d 条建议", len(result.get("suggestions", [])))
    except Exception as exc:
        logger.error("suggestions-refresh failed: %s", exc, exc_info=True)
    finally:
        record_duration(JOB_ID, int((time.time() - t0) * 1000))


def register(sched: AsyncIOScheduler) -> None:
    """每天 2 次:13:00 UTC(美股盘前)、22:00 UTC(收盘后)。
    build_suggestions 含辩论耗时数分钟,misfire_grace_time 给足 10 分钟。"""
    sched.add_job(
        run_suggestions_job,
        trigger=CronTrigger(hour="13,22", minute=0, timezone="UTC"),
        id=JOB_ID,
        name="建议批次定时预生成(美股盘前+收盘后)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
```

- [ ] **Step 4: 跑测试验证通过**

Run:`.venv/bin/python -m pytest tests/test_suggestions_worker.py -v`
Expected: PASS,2 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/app/workers/suggestions_worker.py backend/tests/test_suggestions_worker.py
git commit -m "feat(debate): suggestions_worker — 建议批次定时预生成"
```
(append Co-Authored-By trailer.)

---

## Task 9: 注册 worker + 全链路核对

**Files:**
- Modify: `backend/app/workers/scheduler.py`

- [ ] **Step 1: 注册 suggestions_worker**

在 `backend/app/workers/scheduler.py` 的 `start_scheduler` 函数里:

(a) 延迟 import 块(`from app.workers.refresh_worker import register as register_refresh` 那一组)中追加一行:

```python
    from app.workers.suggestions_worker import register as register_suggestions
```

(b) register 调用序列(`register_daily_baseline(sched)` 之后)追加一行:

```python
    register_suggestions(sched)
```

- [ ] **Step 2: 确认 app 与 scheduler 正常**

Run(from `backend/`):`.venv/bin/python -c "import app.main; from app.workers.suggestions_worker import register; print('imports OK')"`
Expected: 输出 `imports OK`,无 import 错误。

- [ ] **Step 3: 跑全量测试**

Run(from `backend/`):`.venv/bin/python -m pytest tests/ -v`
Expected: 全部 PASS(约 62 个用例)。

- [ ] **Step 4: ruff 静态检查**

Run(from `backend/`):`.venv/bin/python -m ruff check app/services/suggestion_debate.py app/services/suggestions.py app/workers/suggestions_worker.py app/workers/scheduler.py app/models/suggestion.py`
Expected: 无报错。若有 import 排序问题,`ruff check --fix <files>` 修复后重新 commit。

- [ ] **Step 5: 确认 db 迁移生效**

Run(from `backend/`):`.venv/bin/python -c "from app.db import init_db; init_db()"` 然后
`.venv/bin/python -c "import sqlite3; print([r[1] for r in sqlite3.connect('data/trading.db').execute('PRAGMA table_info(suggestion)')])"`
Expected: 输出列表里含 `debate_json`。

- [ ] **Step 6: Commit**

```bash
git add backend/app/workers/scheduler.py
git commit -m "feat(debate): scheduler 注册 suggestions_worker"
```
(append Co-Authored-By trailer。若 Step 4 有 ruff --fix 改动,一并 `git add -A` 提交。)

---

## 自检清单(写计划后已核对)

- **Spec 覆盖**:§4.1 模块划分→Task 1-9;§4.2 数据流→Task 7(build_suggestions)+ Task 8(worker);
  §5 辩论输入合成→Task 5(`_synth_inputs`/`_debate_one`/`debate_batch`,期权跳过);
  §6.1 一致性分类→Task 2;§6.2 urgency/thesis→Task 3 + Task 4;§6.3 存储→Task 4(`debate` 字段)+ Task 6(持久化);
  §7 schema→Task 1;§8 build_suggestions→Task 7;§9 worker→Task 8 + Task 9;
  §10 错误兜底→Task 5(per-symbol 隔离 + disabled no-op)+ Task 7(debate_batch try/except);
  §11 测试→各 Task 的 TDD 步骤;§12 config→无新增(复用,Task 5 用 `debate_enabled`/`debate_max_workers`)。
- **占位符**:无 TBD/TODO;每个代码步骤含完整代码。
- **类型一致**:`classify_consistency(action, verdict)`、`downgrade_urgency(urgency)`、
  `debate_annotation(consistency, action, verdict)`、`apply_debate(suggestion, verdict)`、
  `debate_batch(suggestions)`、`_debate_one(symbol)`、`_synth_inputs(symbol)` 签名跨 Task 一致;
  in-flight 建议 dict 用 `debate` 键(dict),DB 列 `debate_json`(JSON string)—— 与现有
  `affordability`↔`affordability_json` 同模式。
- **偏离 spec 的小调整**:spec §4.1 写 `debate_batch(suggestions, db)`,本计划落地为
  `debate_batch(suggestions)` —— 实现中 `build_position_context` 自开 DB session,`debate_batch`
  本身不需要 db 参数,去掉更干净。
