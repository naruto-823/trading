# 辩论评分引擎 Phase 1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建共享的「看多/看空辩论 + 判官打分」评分内核,并接入快讯推送侧(macro_pusher + jin10_browser_worker),作为现有 Quick Assess triage 的第二阶段。

**Architecture:** 两阶段门控 —— 现有 `score_relevance()` 单次 Haiku triage 每条快讯都跑;`should_escalate()` 判定高 stakes 的快讯升级,异步丢进有界线程池;`run_debate()` 跑 research(websearch)→ 看多 Haiku ∥ 看空 Haiku → 判官 Sonnet,产出 verdict;consumer 把 verdict 落库并推一条完整 Bark。全链路 fail-open,任何失败退化为 triage 行为。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.0 / SQLite / `anthropic` SDK(原生 messages + web_search 工具)/ `concurrent.futures.ThreadPoolExecutor` / pytest + pytest-asyncio。

**Spec:** `docs/superpowers/specs/2026-05-20-debate-scorer-design.md`(Phase 1 = §4–7、§9–13 的快讯侧;§8 建议侧属 Phase 2,本计划不含)。

---

## 文件结构

**新建:**
- `backend/tests/__init__.py` — 测试包
- `backend/tests/conftest.py` — pytest fixture(内存 SQLite session)
- `backend/app/services/debate_research.py` — `gather_research()`:websearch 拉研究简报,fail-soft
- `backend/app/services/debate_scorer.py` — `should_escalate()` / `run_debate()` / 看多看空/判官/归一化/`build_position_context()`,纯辩论逻辑,不碰 Bark
- `backend/app/services/debate_queue.py` — 有界线程池执行器 + `submit_debate()` / `process_escalated_event()` / `format_debate_push()` / `reconcile_stale_debates()`
- `backend/tests/test_debate_escalation.py`
- `backend/tests/test_debate_research.py`
- `backend/tests/test_debate_scorer.py`
- `backend/tests/test_debate_queue.py`
- `backend/tests/test_config.py`
- `backend/tests/test_event_notification_model.py`
- `backend/tests/test_macro_pusher_escalation.py`
- `backend/scripts/eval_debate.py` — 人工评测脚本(非 CI)

**修改:**
- `backend/pyproject.toml` — 加 pytest 配置
- `backend/app/config.py` — 加 `debate_*` 设置项
- `backend/app/models/event_notification.py` — 加 `debate_json` 列
- `backend/app/db.py` — `_apply_lightweight_migrations` 加 `debate_json`
- `backend/app/services/macro_pusher.py` — 升级分支
- `backend/app/workers/jin10_browser_worker.py` — 升级分支
- `backend/app/workers/macro_flash_worker.py`(经 `macro_pusher.run_macro_flash`)— 僵尸行对账

**约定:** 所有 `pytest` 命令在 `backend/` 目录下执行。

---

## Task 1: 测试基础设施

**Files:**
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/conftest.py`
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: 建测试包**

创建 `backend/tests/__init__.py`,内容为空文件。

- [ ] **Step 2: 写 conftest.py**

创建 `backend/tests/conftest.py`:

```python
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  -- 触发所有 ORM 模型注册到 Base.metadata
from app.db import Base


@pytest.fixture
def db_session() -> Session:
    """内存 SQLite session,每个测试 fresh 建表。StaticPool 保证内存库跨连接存活。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    test_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = test_session_local()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
```

- [ ] **Step 3: 配 pytest**

在 `backend/pyproject.toml` 末尾追加:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
asyncio_mode = "auto"
```

- [ ] **Step 4: 写一个冒烟测试验证 fixture 可用**

创建 `backend/tests/test_config.py`(Task 2 会扩它,先放冒烟用例):

```python
from app.config import Settings


def test_settings_loads():
    s = Settings()
    assert s.relevance_threshold >= 0
```

- [ ] **Step 5: 跑测试验证基础设施通**

Run(在 `backend/`):`python -m pytest tests/test_config.py -v`
Expected: PASS,1 passed。

- [ ] **Step 6: Commit**

```bash
git add backend/tests/__init__.py backend/tests/conftest.py backend/tests/test_config.py backend/pyproject.toml
git commit -m "test: 建 pytest 基础设施(内存 SQLite fixture)"
```

---

## Task 2: Config — debate 设置项

**Files:**
- Modify: `backend/app/config.py:52`(在 `relevance_model` 之后插入)
- Modify: `backend/tests/test_config.py`

- [ ] **Step 1: 写失败测试**

把 `backend/tests/test_config.py` 改为:

```python
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
```

- [ ] **Step 2: 跑测试验证失败**

Run:`python -m pytest tests/test_config.py::test_debate_settings_defaults -v`
Expected: FAIL,`AttributeError: 'Settings' object has no attribute 'debate_enabled'`

- [ ] **Step 3: 加 config 字段**

在 `backend/app/config.py` 的 `relevance_model: str = "claude-haiku-4-5-20251001"` 这一行之后、`# Database` 之前插入:

```python

    # —— 辩论评分引擎 (debate scorer) ——
    # spec: docs/superpowers/specs/2026-05-20-debate-scorer-design.md
    debate_enabled: bool = True
    debate_bull_model: str = "claude-haiku-4-5-20251001"
    debate_bear_model: str = "claude-haiku-4-5-20251001"
    debate_judge_model: str = "claude-sonnet-4-6"
    # 升级判定:triage score 落在 [lo, hi] 临界带 → 升级辩论
    debate_escalate_score_lo: int = 35
    debate_escalate_score_hi: int = 65
    # 源 importance ≥ 此值(如 FOMC/CPI)→ 升级辩论
    debate_escalate_min_importance: int = 5
    debate_timeout_seconds: int = 90
    debate_zombie_minutes: int = 5  # debating 行超过此分钟数 → 对账强制收尾
    debate_max_workers: int = 2
    debate_websearch_enabled: bool = True
    debate_websearch_max_uses: int = 3
    debate_daily_cap: int = 0  # 0=不限;>0 时超额当天降级走 triage
```

- [ ] **Step 4: 跑测试验证通过**

Run:`python -m pytest tests/test_config.py -v`
Expected: PASS,2 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/tests/test_config.py
git commit -m "feat(debate): 加 debate_* config 设置项"
```

---

## Task 3: Schema — event_notification.debate_json 列

**Files:**
- Modify: `backend/app/models/event_notification.py:53`(在 `affected_tickers_json` 之后)
- Modify: `backend/app/db.py`(`_apply_lightweight_migrations` 的 `added` 列表)
- Create: `backend/tests/test_event_notification_model.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_event_notification_model.py`:

```python
from datetime import datetime

from app.models.event_notification import EventNotification


def test_event_notification_debate_json_roundtrips(db_session):
    ev = EventNotification(
        id="t1",
        event_hash="h1",
        notified_at=datetime.utcnow(),
        importance="high",
        title="测试快讯",
        body="正文",
        push_status="debating",
        debate_json='{"winning_side": "bull"}',
    )
    db_session.add(ev)
    db_session.commit()

    got = db_session.query(EventNotification).filter_by(id="t1").first()
    assert got.push_status == "debating"
    assert got.debate_json == '{"winning_side": "bull"}'
```

- [ ] **Step 2: 跑测试验证失败**

Run:`python -m pytest tests/test_event_notification_model.py -v`
Expected: FAIL,`TypeError: 'debate_json' is an invalid keyword argument for EventNotification`

- [ ] **Step 3: 模型加列**

在 `backend/app/models/event_notification.py` 的 `affected_tickers_json` 那一行之后插入:

```python

    # 辩论评分结果(升级到 debate_scorer 的快讯)
    # JSON: {research_brief, bull, bear, judge_reasoning, winning_side}
    debate_json: Mapped[str | None] = mapped_column(Text, nullable=True)
```

并把第 37 行注释更新为(说明 push_status 多了一个取值):

```python
    # sent / failed / skipped_low_relevance / debating(已升级辩论,等 verdict)
```

- [ ] **Step 4: 轻量迁移加列**

在 `backend/app/db.py` 的 `_apply_lightweight_migrations` 的 `added` 列表里,`("event_notification", "affected_tickers_json", "TEXT")` 这一项之后追加:

```python
        ("event_notification", "debate_json", "TEXT"),
```

- [ ] **Step 5: 跑测试验证通过**

Run:`python -m pytest tests/test_event_notification_model.py -v`
Expected: PASS,1 passed。

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/event_notification.py backend/app/db.py backend/tests/test_event_notification_model.py
git commit -m "feat(debate): event_notification 加 debate_json 列"
```

---

## Task 4: 升级判定 should_escalate

**Files:**
- Create: `backend/app/services/debate_scorer.py`
- Create: `backend/tests/test_debate_escalation.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_debate_escalation.py`:

```python
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
```

- [ ] **Step 2: 跑测试验证失败**

Run:`python -m pytest tests/test_debate_escalation.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'app.services.debate_scorer'`

- [ ] **Step 3: 写 debate_scorer.py 骨架 + should_escalate**

创建 `backend/app/services/debate_scorer.py`:

```python
"""辩论评分内核 —— 看多/看空 agent 对抗 + 判官裁决

阶段 2 评分(阶段 1 是 relevance_scorer 的单次 Haiku triage)。
仅当 should_escalate() 为真时由 debate_queue 异步调起。
全链路 fail-open:任何失败回退 triage,绝不丢信号。

spec: docs/superpowers/specs/2026-05-20-debate-scorer-design.md
"""

from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)


def should_escalate(triage: dict, source_importance: int) -> bool:
    """两阶段门控:triage 结果 → 要不要升级到完整辩论。

    triage: relevance_scorer.score_relevance() 的返回 dict
    source_importance: 快讯源标的重要度(MacroFlash.importance,int)
    """
    if not settings.debate_enabled:
        return False
    # triage 自己挂了(fail-open),别再升级 —— 让它走快路兜底推送
    if triage.get("model") == "fail-open":
        return False
    if triage.get("affected_tickers"):
        return True
    if source_importance >= settings.debate_escalate_min_importance:
        return True
    score = int(triage.get("score", 0))
    return settings.debate_escalate_score_lo <= score <= settings.debate_escalate_score_hi
```

- [ ] **Step 4: 跑测试验证通过**

Run:`python -m pytest tests/test_debate_escalation.py -v`
Expected: PASS,8 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/debate_scorer.py backend/tests/test_debate_escalation.py
git commit -m "feat(debate): should_escalate 两阶段门控判定"
```

---

## Task 5: 研究简报 gather_research

**Files:**
- Create: `backend/app/services/debate_research.py`
- Create: `backend/tests/test_debate_research.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_debate_research.py`:

```python
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
```

- [ ] **Step 2: 跑测试验证失败**

Run:`python -m pytest tests/test_debate_research.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'app.services.debate_research'`

- [ ] **Step 3: 写 debate_research.py**

创建 `backend/app/services/debate_research.py`:

```python
"""辩论用的实时研究简报 —— 带 web_search 工具的 Haiku 调用

fail-soft:websearch 禁用 / 未配 key / 调用失败 → 返回 ""(辩论降级为无外部数据)。
spec: docs/superpowers/specs/2026-05-20-debate-scorer-design.md §5
"""

from __future__ import annotations

import logging

from anthropic import Anthropic

from app.config import settings

logger = logging.getLogger(__name__)

RESEARCH_SYSTEM_PROMPT = """你是金融研究助理。用 web_search 查聚焦标的的近期实时情况:
近 1-2 周价格走势、关键催化事件、多空双方各自的论据。
输出一段 300-500 字中文研究简报,客观陈列正反两面事实,不下结论、不给买卖建议。"""


def gather_research(content: str, tickers: list[str]) -> str:
    """拉研究简报。永不抛异常 —— 失败返回 ""。"""
    if not settings.debate_websearch_enabled or not settings.anthropic_api_key:
        return ""
    focus = ", ".join(tickers) if tickers else "快讯涉及的宏观主题"
    try:
        client = Anthropic(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url or None,
            timeout=45.0,
        )
        resp = client.messages.create(
            model=settings.debate_bull_model,  # 用 Haiku 跑研究
            max_tokens=900,
            system=RESEARCH_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"快讯:{content[:600]}\n\n聚焦标的:{focus}",
            }],
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": settings.debate_websearch_max_uses,
            }],
        )
        brief = "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        ).strip()
        return brief[:1500]
    except Exception as exc:
        logger.warning("debate_research fail-soft: %s", exc)
        return ""
```

> 注:`web_search` 工具类型字符串 `web_search_20250305` 是 Anthropic 当前版本;实现时若 SDK 报版本不符,用 `claude-api` skill 核对最新工具版本号后替换。

- [ ] **Step 4: 跑测试验证通过**

Run:`python -m pytest tests/test_debate_research.py -v`
Expected: PASS,3 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/debate_research.py backend/tests/test_debate_research.py
git commit -m "feat(debate): gather_research — websearch 研究简报(fail-soft)"
```

---

## Task 6: 看多/看空辩手 _run_advocate

**Files:**
- Modify: `backend/app/services/debate_scorer.py`
- Modify: `backend/tests/test_debate_scorer.py`(新建)

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_debate_scorer.py`:

```python
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
```

- [ ] **Step 2: 跑测试验证失败**

Run:`python -m pytest tests/test_debate_scorer.py -v`
Expected: FAIL,`AttributeError: module 'app.services.debate_scorer' has no attribute '_run_advocate'`

- [ ] **Step 3: 加 Anthropic 客户端/JSON 工具 + 辩手 prompt + _run_advocate**

在 `backend/app/services/debate_scorer.py` 顶部 import 区改为:

```python
from __future__ import annotations

import json
import logging

from anthropic import Anthropic

from app.config import settings

logger = logging.getLogger(__name__)
```

在 `should_escalate` 函数之后追加:

```python
# —————————————————— 辩手(看多 / 看空)——————————————————

BULL_SYSTEM_PROMPT = """你是『看多辩手』。给一条金融快讯 + 用户持仓 + 研究简报,
你的任务:为「这对用户持仓是利好 / 应看涨」构建最强论证 —— 即使你内心不完全认同,
也要找出最有力的多方理由。严格输出 JSON,不要 markdown 包裹:
{
  "stance_score": <0-100,你为多方立场打的强度分>,
  "key_points": ["论点1", "论点2", ...最多4条],
  "strongest_argument": "最强的一条多方论证(<300字)",
  "risks_to_own_view": "诚实指出多方立场最大的软肋(<200字)"
}"""

BEAR_SYSTEM_PROMPT = """你是『看空辩手』。给一条金融快讯 + 用户持仓 + 研究简报,
你的任务:为「这对用户持仓是利空 / 应看跌」构建最强论证 —— 即使你内心不完全认同,
也要找出最有力的空方理由。严格输出 JSON,不要 markdown 包裹:
{
  "stance_score": <0-100,你为空方立场打的强度分>,
  "key_points": ["论点1", "论点2", ...最多4条],
  "strongest_argument": "最强的一条空方论证(<300字)",
  "risks_to_own_view": "诚实指出空方立场最大的软肋(<200字)"
}"""


def _client(timeout: float) -> Anthropic:
    return Anthropic(
        api_key=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
        timeout=timeout,
    )


def _extract_json(resp) -> str:
    """从 messages 响应里取文本并剥掉 ``` 围栏。"""
    text = "".join(
        b.text for b in resp.content if getattr(b, "type", "") == "text"
    ).strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl > 0:
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return text


def _run_advocate(
    side: str, model: str, content: str, position_ctx: str, brief: str
) -> dict | None:
    """跑一个辩手(side='bull' 或 'bear')。失败返回 None。"""
    system = BULL_SYSTEM_PROMPT if side == "bull" else BEAR_SYSTEM_PROMPT
    user = (
        f"{position_ctx}\n\n快讯:{content[:600]}\n\n"
        f"外部研究简报:{brief or '(无外部数据,仅凭快讯判断)'}"
    )
    try:
        resp = _client(timeout=float(settings.debate_timeout_seconds)).messages.create(
            model=model,
            max_tokens=600,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        data = json.loads(_extract_json(resp))
        return {
            "side": side,
            "stance_score": max(0, min(100, int(data.get("stance_score", 50)))),
            "key_points": [str(p)[:120] for p in (data.get("key_points") or [])][:4],
            "strongest_argument": str(data.get("strongest_argument", ""))[:300],
            "risks_to_own_view": str(data.get("risks_to_own_view", ""))[:200],
        }
    except Exception as exc:
        logger.warning("debate advocate %s failed: %s", side, exc)
        return None
```

- [ ] **Step 4: 跑测试验证通过**

Run:`python -m pytest tests/test_debate_scorer.py -v`
Expected: PASS,2 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/debate_scorer.py backend/tests/test_debate_scorer.py
git commit -m "feat(debate): 看多/看空辩手 _run_advocate"
```

---

## Task 7: 判官 + run_debate 编排 + fail-open 回退

**Files:**
- Modify: `backend/app/services/debate_scorer.py`
- Modify: `backend/tests/test_debate_scorer.py`

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_debate_scorer.py` 末尾追加:

```python
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
```

- [ ] **Step 2: 跑测试验证失败**

Run:`python -m pytest tests/test_debate_scorer.py::test_run_debate_happy_path -v`
Expected: FAIL,`AttributeError: module 'app.services.debate_scorer' has no attribute 'run_debate'`

- [ ] **Step 3: 加判官 + run_debate + 归一化/回退**

在 `backend/app/services/debate_scorer.py` 顶部 import 区追加:

```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from app.services.debate_research import gather_research
```

在文件末尾追加:

```python
# —————————————————— 判官 + 编排 ——————————————————

JUDGE_SYSTEM_PROMPT = """你是中立『判官』。看多辩手和看空辩手各自给出了论证。
你的任务:权衡双方论据强弱、证据质量,给出最终裁决。
不要简单折中 —— 哪边论据更扎实就倒向哪边。
严格输出 JSON,不要 markdown 包裹:
{
  "relevance": "direct|indirect|noise",
  "score": <0-100 综合影响分,推送门槛用这个>,
  "sentiment": "positive|negative|neutral",
  "direction": "bullish|bearish|neutral",
  "confidence": <0-100 你对裁决的把握>,
  "affected_tickers": ["MSFT"],
  "reason": "<30字内结论>",
  "bull_case": "<采纳/转述的多方最强点>",
  "bear_case": "<采纳/转述的空方最强点>",
  "judge_reasoning": "<你为何这样裁:谁更有理、哪边证据弱>",
  "winning_side": "bull|bear|balanced"
}"""

_VALID_RELEVANCE = ("direct", "indirect", "noise")
_VALID_SENTIMENT = ("positive", "negative", "neutral")
_VALID_DIRECTION = ("bullish", "bearish", "neutral")


def _format_advocate(label: str, adv: dict | None) -> str:
    if adv is None:
        return f"{label}辩手:(缺席,本方陈词缺失)"
    points = ";".join(adv.get("key_points") or [])
    return (
        f"{label}辩手(立场强度 {adv.get('stance_score', 50)}):\n"
        f"  最强论证:{adv.get('strongest_argument', '')}\n"
        f"  论点:{points}\n"
        f"  自承软肋:{adv.get('risks_to_own_view', '')}"
    )


def _run_judge(
    content: str, position_ctx: str, brief: str,
    bull: dict | None, bear: dict | None,
) -> dict | None:
    """跑判官。失败返回 None。"""
    user = (
        f"{position_ctx}\n\n快讯:{content[:600]}\n\n"
        f"外部研究简报:{brief or '(无)'}\n\n"
        f"{_format_advocate('看多', bull)}\n\n{_format_advocate('看空', bear)}"
    )
    try:
        resp = _client(timeout=float(settings.debate_timeout_seconds)).messages.create(
            model=settings.debate_judge_model,
            max_tokens=700,
            system=JUDGE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        return json.loads(_extract_json(resp))
    except Exception as exc:
        logger.warning("debate judge failed: %s", exc)
        return None


def _verdict_from_triage(triage: dict, note: str) -> dict:
    """fail-open 回退:辩论挂了,用 triage 结果造一个 verdict。"""
    return {
        "relevance": triage.get("relevance", "indirect"),
        "score": int(triage.get("score", 0)),
        "sentiment": triage.get("sentiment", "neutral"),
        "direction": triage.get("direction", "neutral"),
        "confidence": int(triage.get("confidence", 50)),
        "affected_tickers": list(triage.get("affected_tickers") or []),
        "reason": f"[辩论降级] {note} · {triage.get('reason', '')}"[:200],
        "bull_case": "", "bear_case": "",
        "judge_reasoning": f"辩论未完成({note}),回退 triage 快评",
        "winning_side": "balanced",
        "model": "debate-degraded",
    }


def _normalize_verdict(
    judged: dict, bull: dict | None, bear: dict | None, brief: str
) -> dict:
    """把判官原始输出 clamp/规范成 DebateVerdict。"""
    relevance = judged.get("relevance")
    if relevance not in _VALID_RELEVANCE:
        relevance = "indirect"
    sentiment = judged.get("sentiment")
    if sentiment not in _VALID_SENTIMENT:
        sentiment = "neutral"
    direction = judged.get("direction")
    if direction not in _VALID_DIRECTION:
        direction = "neutral"
    winning = judged.get("winning_side")
    if winning not in ("bull", "bear", "balanced"):
        winning = "balanced"
    affected = judged.get("affected_tickers") or []
    if not isinstance(affected, list):
        affected = []
    affected = [str(t).upper().strip() for t in affected if t][:3]
    return {
        "relevance": relevance,
        "score": max(0, min(100, int(judged.get("score", 0)))),
        "sentiment": sentiment,
        "direction": direction,
        "confidence": max(0, min(100, int(judged.get("confidence", 50)))),
        "affected_tickers": affected,
        "reason": str(judged.get("reason", ""))[:200],
        "bull_case": str(judged.get("bull_case", ""))[:400],
        "bear_case": str(judged.get("bear_case", ""))[:400],
        "judge_reasoning": str(judged.get("judge_reasoning", ""))[:600],
        "winning_side": winning,
        "model": "debate",
    }


def run_debate(content: str, triage: dict, position_ctx: str) -> dict:
    """跑完整辩论。永远返回一个 verdict dict(失败 fail-open 回退 triage)。

    verdict 是现有 scorer 字段(relevance/score/sentiment/direction/confidence/
    affected_tickers/reason)的超集,额外含 bull_case/bear_case/judge_reasoning/
    winning_side/model。
    """
    tickers = list(triage.get("affected_tickers") or [])
    brief = gather_research(content, tickers)

    bull: dict | None = None
    bear: dict | None = None
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="advocate") as ex:
            f_bull = ex.submit(
                _run_advocate, "bull", settings.debate_bull_model,
                content, position_ctx, brief,
            )
            f_bear = ex.submit(
                _run_advocate, "bear", settings.debate_bear_model,
                content, position_ctx, brief,
            )
            timeout = float(settings.debate_timeout_seconds)
            bull = f_bull.result(timeout=timeout)
            bear = f_bear.result(timeout=timeout)
    except FutureTimeout:
        logger.warning("debate advocates timed out")
    except Exception as exc:
        logger.warning("debate advocates error: %s", exc)

    if bull is None and bear is None:
        return _verdict_from_triage(triage, "多空均失败")

    judged = _run_judge(content, position_ctx, brief, bull, bear)
    if judged is None:
        return _verdict_from_triage(triage, "判官失败")

    return _normalize_verdict(judged, bull, bear, brief)
```

- [ ] **Step 4: 跑测试验证通过**

Run:`python -m pytest tests/test_debate_scorer.py -v`
Expected: PASS,5 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/debate_scorer.py backend/tests/test_debate_scorer.py
git commit -m "feat(debate): 判官 + run_debate 编排,fail-open 回退 triage"
```

---

## Task 8: build_position_context

**Files:**
- Modify: `backend/app/services/debate_scorer.py`
- Modify: `backend/tests/test_debate_scorer.py`

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_debate_scorer.py` 末尾追加:

```python
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
```

> 注:测试里 `patch SessionLocal` 让其返回 fixture session;`build_position_context` 内部对返回对象调 `.close()` 是幂等的,fixture 的 teardown 会再 close 一次,无害。

- [ ] **Step 2: 跑测试验证失败**

Run:`python -m pytest tests/test_debate_scorer.py::test_build_position_context_no_positions -v`
Expected: FAIL,`AttributeError: module 'app.services.debate_scorer' has no attribute 'build_position_context'`

- [ ] **Step 3: 加 build_position_context**

在 `backend/app/services/debate_scorer.py` 顶部 import 区追加:

```python
from app.db import SessionLocal
from app.services.positions import list_positions
```

在文件末尾追加:

```python
# —————————————————— 持仓上下文 ——————————————————

def build_position_context(affected_tickers: list[str]) -> str:
    """给辩论用的持仓上下文:全部重仓概览 + 受影响标的的成本/盈亏明细。

    比 triage 的持仓上下文更丰富 —— 让辩手能像人一样推理(如"已接近回本")。
    """
    affected = {t.upper() for t in (affected_tickers or [])}
    db = SessionLocal()
    try:
        positions = list_positions(db)
        stocks = sorted(
            [p for p in positions if len(p.symbol) <= 8 and abs(p.market_value) > 0],
            key=lambda p: abs(p.market_value),
            reverse=True,
        )[:10]
        if not stocks:
            return "用户当前无持仓"

        overview = "用户重仓(按市值倒序): " + ", ".join(
            f"{p.symbol}({p.name})" for p in stocks
        )

        detail_lines = []
        for p in stocks:
            base = p.symbol.split(".")[0].upper()
            if base in affected or p.symbol.upper() in affected:
                pnl_pct = round(p.unrealized_pnl_ratio * 100, 1)
                detail_lines.append(
                    f"  {p.symbol}: {p.quantity}股 成本{p.cost_price} "
                    f"现价{p.current_price} 盈亏{pnl_pct}%"
                )
        if detail_lines:
            return overview + "\n受影响标的明细:\n" + "\n".join(detail_lines)
        return overview
    finally:
        db.close()
```

- [ ] **Step 4: 跑测试验证通过**

Run:`python -m pytest tests/test_debate_scorer.py -v`
Expected: PASS,7 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/debate_scorer.py backend/tests/test_debate_scorer.py
git commit -m "feat(debate): build_position_context — 受影响标的带盈亏明细"
```

---

## Task 9: debate_queue — 执行器 + 消费者 + 推送格式化

**Files:**
- Create: `backend/app/services/debate_queue.py`
- Create: `backend/tests/test_debate_queue.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_debate_queue.py`:

```python
import json
from datetime import datetime, timedelta
from unittest.mock import patch

from app.models.event_notification import EventNotification
from app.services import debate_queue


def _debating_row(db, **over):
    row = EventNotification(
        id=over.get("id", "ev1"),
        event_hash=over.get("event_hash", "h1"),
        notified_at=over.get("notified_at", datetime.utcnow()),
        symbol="INTW",
        importance="high",
        title="英特尔大涨",
        body="英特尔美股盘前涨超4%",
        source_title="[jin10] 英特尔大涨",
        push_status="debating",
        relevance="direct", relevance_score=55, relevance_reason="点名持仓",
        sentiment="neutral", direction="neutral", confidence=50,
        affected_tickers_json='["INTW"]',
    )
    db.add(row)
    db.commit()
    return row


_VERDICT = {
    "relevance": "direct", "score": 72, "sentiment": "positive",
    "direction": "bullish", "confidence": 65, "affected_tickers": ["INTW"],
    "reason": "板块反弹", "bull_case": "贸易缓和", "bear_case": "2x decay",
    "judge_reasoning": "多方更扎实", "winning_side": "bull", "model": "debate",
}


def test_process_escalated_event_pushes_and_updates(db_session):
    _debating_row(db_session)
    with patch.object(debate_queue, "SessionLocal", return_value=db_session), \
         patch.object(debate_queue, "run_debate", return_value=_VERDICT), \
         patch.object(debate_queue, "build_position_context", return_value="ctx"), \
         patch.object(debate_queue, "send_bark", return_value={"ok": True}) as mock_bark:
        debate_queue.process_escalated_event("ev1")

    mock_bark.assert_called_once()
    row = db_session.query(EventNotification).filter_by(id="ev1").first()
    assert row.push_status == "sent"
    assert row.relevance_score == 72
    assert row.direction == "bullish"
    assert json.loads(row.debate_json)["winning_side"] == "bull"


def test_process_escalated_event_low_score_no_push(db_session):
    _debating_row(db_session)
    low = {**_VERDICT, "score": 20}
    with patch.object(debate_queue, "SessionLocal", return_value=db_session), \
         patch.object(debate_queue, "run_debate", return_value=low), \
         patch.object(debate_queue, "build_position_context", return_value="ctx"), \
         patch.object(debate_queue, "send_bark") as mock_bark:
        debate_queue.process_escalated_event("ev1")

    mock_bark.assert_not_called()
    row = db_session.query(EventNotification).filter_by(id="ev1").first()
    assert row.push_status == "skipped_low_relevance"


def test_process_escalated_event_ignores_non_debating_row(db_session):
    row = _debating_row(db_session)
    row.push_status = "sent"
    db_session.commit()
    with patch.object(debate_queue, "SessionLocal", return_value=db_session), \
         patch.object(debate_queue, "run_debate") as mock_run:
        debate_queue.process_escalated_event("ev1")
    mock_run.assert_not_called()


def test_reconcile_stale_debates_finalizes_zombie(db_session):
    _debating_row(db_session, notified_at=datetime.utcnow() - timedelta(minutes=10))
    with patch.object(debate_queue, "send_bark", return_value={"ok": True}):
        n = debate_queue.reconcile_stale_debates(db_session)
    assert n == 1
    row = db_session.query(EventNotification).filter_by(id="ev1").first()
    # relevance_score=55 ≥ 阈值50 → 用 triage 分推送收尾
    assert row.push_status in ("sent", "failed")


def test_reconcile_skips_fresh_debating_row(db_session):
    _debating_row(db_session, notified_at=datetime.utcnow())
    n = debate_queue.reconcile_stale_debates(db_session)
    assert n == 0
    row = db_session.query(EventNotification).filter_by(id="ev1").first()
    assert row.push_status == "debating"


def test_format_debate_push_layout(db_session):
    row = _debating_row(db_session)
    title, body, level = debate_queue.format_debate_push(row, _VERDICT)
    assert "🧠" in title
    assert "INTW" in title
    assert "判官:看涨" in body
    assert "多:" in body and "空:" in body
    assert level == "timeSensitive"  # importance=high
```

- [ ] **Step 2: 跑测试验证失败**

Run:`python -m pytest tests/test_debate_queue.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'app.services.debate_queue'`

- [ ] **Step 3: 写 debate_queue.py**

创建 `backend/app/services/debate_queue.py`:

```python
"""辩论异步执行 + 消费者

升级的快讯先落一条 push_status="debating" 的 event_notification 行(兼当去重锁),
再 submit_debate(row_id) 进有界线程池。消费者跑辩论 → 推 Bark → 更新同一行。
僵尸行(debating 超时)由 reconcile_stale_debates 用 triage 分强制收尾。

spec: docs/superpowers/specs/2026-05-20-debate-scorer-design.md §6-7、§9
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models.event_notification import EventNotification
from app.services.debate_scorer import build_position_context, run_debate
from app.services.notify import send_bark

logger = logging.getLogger(__name__)

_DIR_EMOJI = {"bullish": "📈", "bearish": "📉", "neutral": ""}
_DIR_LABEL = {"bullish": "看涨", "bearish": "看跌", "neutral": "中性"}

_executor: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=settings.debate_max_workers, thread_name_prefix="debate"
        )
    return _executor


def submit_debate(event_id: str) -> None:
    """把一条 debating 行的辩论任务丢进线程池(不阻塞调用方)。"""
    _get_executor().submit(_safe_process, event_id)


def _safe_process(event_id: str) -> None:
    try:
        process_escalated_event(event_id)
    except Exception:
        logger.exception("debate process crashed: %s", event_id)


def _triage_from_row(ev: EventNotification) -> dict:
    """从 debating 行重建 triage dict(行创建时已填入 triage 字段)。"""
    try:
        affected = json.loads(ev.affected_tickers_json) if ev.affected_tickers_json else []
    except (ValueError, TypeError):
        affected = []
    return {
        "relevance": ev.relevance or "indirect",
        "score": ev.relevance_score or 0,
        "sentiment": ev.sentiment or "neutral",
        "direction": ev.direction or "neutral",
        "confidence": ev.confidence or 50,
        "affected_tickers": affected,
        "reason": ev.relevance_reason or "",
        "model": settings.relevance_model,
    }


def format_debate_push(ev: EventNotification, verdict: dict) -> tuple[str, str, str]:
    """构造辩论结果的 Bark (title, body, level)。"""
    dir_emoji = _DIR_EMOJI.get(verdict["direction"], "")
    degraded = "[辩论降级]" if verdict.get("model") != "debate" else ""
    affected = verdict.get("affected_tickers") or []
    ticker_part = f"{affected[0]} " if affected else ""
    title = f"🧠{dir_emoji}{degraded} {ticker_part}{ev.title[:25]}"

    lines = [
        f"判官:{_DIR_LABEL.get(verdict['direction'], '中性')} · "
        f"综合{verdict['score']} · 可信度{verdict['confidence']}%"
    ]
    if verdict.get("bull_case"):
        lines.append(f"多: {verdict['bull_case'][:80]}")
    if verdict.get("bear_case"):
        lines.append(f"空: {verdict['bear_case'][:80]}")
    if verdict.get("judge_reasoning"):
        lines.append(f"判官: {verdict['judge_reasoning'][:120]}")
    lines.append(ev.title)
    body = "\n".join(lines)[:600]

    level = "timeSensitive" if ev.importance == "high" else "active"
    return title, body, level


def _apply_verdict(db: Session, ev: EventNotification, verdict: dict) -> None:
    """把 verdict 写回行,按阈值决定推不推。"""
    ev.relevance = verdict["relevance"]
    ev.relevance_score = verdict["score"]
    ev.relevance_reason = verdict["reason"]
    ev.sentiment = verdict["sentiment"]
    ev.direction = verdict["direction"]
    ev.confidence = verdict["confidence"]
    affected = verdict.get("affected_tickers") or []
    ev.affected_tickers_json = json.dumps(affected, ensure_ascii=False) if affected else None
    ev.symbol = affected[0] if affected else ev.symbol
    ev.debate_json = json.dumps({
        "bull_case": verdict.get("bull_case", ""),
        "bear_case": verdict.get("bear_case", ""),
        "judge_reasoning": verdict.get("judge_reasoning", ""),
        "winning_side": verdict.get("winning_side", "balanced"),
        "model": verdict.get("model", "debate"),
    }, ensure_ascii=False)

    if verdict["score"] < settings.relevance_threshold:
        ev.push_status = "skipped_low_relevance"
        db.commit()
        logger.info("debate skipped [score=%d]: %s", verdict["score"], ev.title[:60])
        return

    title, body, level = format_debate_push(ev, verdict)
    result = send_bark(title, body, level=level, group="market-events", sound="chime")
    ev.body = body
    ev.push_status = "sent" if result["ok"] else "failed"
    ev.push_error = None if result["ok"] else str(result["detail"])[:500]
    db.commit()
    logger.info("debate fired [score=%d dir=%s]: %s",
                verdict["score"], verdict["direction"], ev.title[:60])


def process_escalated_event(event_id: str) -> None:
    """消费一条 debating 行:跑辩论 → 推 Bark → 更新行。"""
    db = SessionLocal()
    try:
        ev = db.query(EventNotification).filter_by(id=event_id).first()
        if ev is None or ev.push_status != "debating":
            return
        triage = _triage_from_row(ev)
        position_ctx = build_position_context(triage["affected_tickers"])
        verdict = run_debate(ev.body, triage, position_ctx)
        _apply_verdict(db, ev, verdict)
    finally:
        db.close()


def reconcile_stale_debates(db: Session) -> int:
    """对账:debating 状态超过 debate_zombie_minutes 的僵尸行,用 triage 分强制收尾。

    返回收尾的行数。由 macro_flash worker 每轮调用。
    """
    cutoff = datetime.utcnow() - timedelta(minutes=settings.debate_zombie_minutes)
    stale = db.query(EventNotification).filter(
        EventNotification.push_status == "debating",
        EventNotification.notified_at < cutoff,
    ).all()
    for ev in stale:
        triage = _triage_from_row(ev)
        verdict = {
            "relevance": triage["relevance"],
            "score": triage["score"],
            "sentiment": triage["sentiment"],
            "direction": triage["direction"],
            "confidence": triage["confidence"],
            "affected_tickers": triage["affected_tickers"],
            "reason": f"[辩论超时降级] {triage['reason']}"[:200],
            "bull_case": "", "bear_case": "",
            "judge_reasoning": "辩论超时,回退 triage 快评",
            "winning_side": "balanced",
            "model": "debate-degraded",
        }
        _apply_verdict(db, ev, verdict)
    if stale:
        logger.warning("reconcile_stale_debates: 收尾 %d 条僵尸行", len(stale))
    return len(stale)
```

- [ ] **Step 4: 跑测试验证通过**

Run:`python -m pytest tests/test_debate_queue.py -v`
Expected: PASS,6 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/debate_queue.py backend/tests/test_debate_queue.py
git commit -m "feat(debate): debate_queue — 执行器/消费者/推送格式化/僵尸行对账"
```

---

## Task 10: 接入 macro_pusher

**Files:**
- Modify: `backend/app/services/macro_pusher.py`
- Create: `backend/tests/test_macro_pusher_escalation.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_macro_pusher_escalation.py`:

```python
from datetime import datetime, timezone
from unittest.mock import patch

from app.models.event_notification import EventNotification
from app.services import macro_pusher
from app.services.macro_feed import MacroFlash


def _flash():
    return MacroFlash(
        time=datetime.now(timezone.utc),
        title="英特尔美股盘前涨超4%",
        content="英特尔美股盘前涨超4%,半导体板块走强",
        importance=4,
        source="jin10",
        tags=[],
    )


_ESCALATING_TRIAGE = {
    "relevance": "direct", "score": 55, "sentiment": "positive",
    "direction": "bullish", "confidence": 60, "affected_tickers": ["INTW"],
    "reason": "点名半导体", "model": "claude-haiku-4-5-20251001",
}


def test_macro_pusher_escalates_to_debate(db_session):
    with patch.object(macro_pusher, "fetch_macro_news", return_value=[_flash()]), \
         patch.object(macro_pusher, "score_relevance", return_value=_ESCALATING_TRIAGE), \
         patch.object(macro_pusher, "submit_debate") as mock_submit, \
         patch.object(macro_pusher, "send_bark") as mock_bark:
        stats = macro_pusher.run_macro_flash(db_session)

    # 升级:落 debating 行 + submit,不直接推
    assert stats["escalated"] == 1
    mock_bark.assert_not_called()
    mock_submit.assert_called_once()
    row = db_session.query(EventNotification).filter_by(push_status="debating").first()
    assert row is not None
    assert row.relevance_score == 55
```

> 注:`MacroFlash` 字段以 `app/services/macro_feed.py` 实际定义为准;若构造签名不符,按该文件的 dataclass 调整测试里的 `_flash()`。

- [ ] **Step 2: 跑测试验证失败**

Run:`python -m pytest tests/test_macro_pusher_escalation.py -v`
Expected: FAIL,`KeyError: 'escalated'`(或 `AttributeError: ... has no attribute 'submit_debate'`)

- [ ] **Step 3: 接入升级分支**

在 `backend/app/services/macro_pusher.py` 的 import 区(`from app.services.relevance_scorer import score_relevance` 之后)追加:

```python
from app.services.debate_queue import reconcile_stale_debates, submit_debate
from app.services.debate_scorer import should_escalate
```

在 `run_macro_flash` 里,把 `stats` 初始化改为(加 `escalated` 键):

```python
    stats = {
        "fetched": 0, "filtered": 0, "deduped": 0,
        "scored_low": 0, "escalated": 0, "fired": 0, "failed": 0,
    }
```

在 `stats["fetched"] = len(items)` 这一行**之前**插入僵尸行对账:

```python
    stats["reconciled"] = reconcile_stale_debates(db)
```

在 `affected_json = ...` / `symbol = ...` / `common_kwargs = dict(...)` 构造完成**之后**、
`if score < settings.relevance_threshold:` 这一行**之前**,插入升级分支:

```python
        # 两阶段门控:高 stakes 快讯升级到完整辩论(异步,不在此处推送)
        if should_escalate(scoring, item.importance):
            rec = EventNotification(
                **common_kwargs,
                importance="high" if item.importance >= 5 else "medium",
                title=item.title[:200],
                body=(item.content or item.title)[:400],
                push_status="debating",
                push_error=None,
            )
            db.add(rec)
            db.commit()
            submit_debate(rec.id)
            stats["escalated"] += 1
            logger.info("macro-flash escalated to debate: %s", item.title[:60])
            continue
```

- [ ] **Step 4: 跑测试验证通过**

Run:`python -m pytest tests/test_macro_pusher_escalation.py -v`
Expected: PASS,1 passed。

- [ ] **Step 5: 跑全量测试确认无回归**

Run:`python -m pytest tests/ -v`
Expected: 全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/macro_pusher.py backend/tests/test_macro_pusher_escalation.py
git commit -m "feat(debate): macro_pusher 接入升级分支 + 僵尸行对账"
```

---

## Task 11: 接入 jin10_browser_worker

**Files:**
- Modify: `backend/app/workers/jin10_browser_worker.py`
- Create: `backend/tests/test_jin10_escalation.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_jin10_escalation.py`:

```python
from unittest.mock import patch

from app.models.event_notification import EventNotification
from app.workers import jin10_browser_worker as jw


_ESCALATING_TRIAGE = {
    "relevance": "direct", "score": 55, "sentiment": "positive",
    "direction": "bullish", "confidence": 60, "affected_tickers": ["MSFT"],
    "reason": "点名微软", "model": "claude-haiku-4-5-20251001",
}


def test_jin10_flash_escalates_to_debate(db_session):
    flash = {"text": "微软发布 AI 新品,股价异动", "id": "999", "is_important": True}
    with patch.object(jw, "SessionLocal", return_value=db_session), \
         patch.object(jw, "score_relevance", return_value=_ESCALATING_TRIAGE), \
         patch.object(jw, "submit_debate") as mock_submit, \
         patch.object(jw, "send_bark") as mock_bark:
        jw._process_flash_sync(flash)

    mock_bark.assert_not_called()
    mock_submit.assert_called_once()
    row = db_session.query(EventNotification).filter_by(push_status="debating").first()
    assert row is not None
    assert row.relevance_score == 55
```

> 注:`db_session` fixture 是单连接内存库;`patch SessionLocal` 让 worker 用它。`_process_flash_sync` 内部对返回 session 调 `.close()` 幂等无害。

- [ ] **Step 2: 跑测试验证失败**

Run:`python -m pytest tests/test_jin10_escalation.py -v`
Expected: FAIL,`AttributeError: module 'app.workers.jin10_browser_worker' has no attribute 'submit_debate'`

- [ ] **Step 3: 接入升级分支**

在 `backend/app/workers/jin10_browser_worker.py` 的 import 区(`from app.services.relevance_scorer import score_relevance` 之后)追加:

```python
from app.services.debate_queue import submit_debate
from app.services.debate_scorer import should_escalate
```

在 `_process_flash_sync` 里,`common_kwargs = dict(...)` 构造完成**之后**、
`if score < settings.relevance_threshold:` 这一行**之前**,插入升级分支:

```python
        # 两阶段门控:高 stakes 快讯升级到完整辩论(异步,不在此处推送)
        if should_escalate(scoring, item.importance):
            rec = EventNotification(
                **common_kwargs,
                importance="high" if flash.get("is_important") else "medium",
                title=content[:200],
                body=content[:500],
                push_status="debating",
                push_error=None,
            )
            db.add(rec)
            db.commit()
            submit_debate(rec.id)
            logger.info("jin10-browser escalated to debate: %s", content[:60])
            return
```

- [ ] **Step 4: 跑测试验证通过**

Run:`python -m pytest tests/test_jin10_escalation.py -v`
Expected: PASS,1 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/app/workers/jin10_browser_worker.py backend/tests/test_jin10_escalation.py
git commit -m "feat(debate): jin10_browser_worker 接入升级分支"
```

---

## Task 12: 人工评测脚本

**Files:**
- Create: `backend/scripts/eval_debate.py`

- [ ] **Step 1: 写评测脚本**

创建 `backend/scripts/eval_debate.py`:

```python
"""辩论评分人工评测 —— 非 CI,本地手跑

用法(在 backend/ 下):python scripts/eval_debate.py
从 event_notification 表取最近 5 条已评分快讯,各跑一次 run_debate,打印 verdict 对眼。
"""

from app.db import SessionLocal
from app.models.event_notification import EventNotification
from app.services.debate_scorer import build_position_context, run_debate


def main() -> None:
    db = SessionLocal()
    try:
        rows = (
            db.query(EventNotification)
            .filter(EventNotification.relevance_score.isnot(None))
            .order_by(EventNotification.notified_at.desc())
            .limit(5)
            .all()
        )
    finally:
        db.close()

    if not rows:
        print("event_notification 表无已评分快讯,跳过")
        return

    for ev in rows:
        triage = {
            "relevance": ev.relevance or "indirect",
            "score": ev.relevance_score or 0,
            "sentiment": ev.sentiment or "neutral",
            "direction": ev.direction or "neutral",
            "confidence": ev.confidence or 50,
            "affected_tickers": [ev.symbol] if ev.symbol else [],
            "reason": ev.relevance_reason or "",
            "model": "eval",
        }
        ctx = build_position_context(triage["affected_tickers"])
        print("=" * 70)
        print(f"快讯: {ev.title[:70]}")
        print(f"triage: score={triage['score']} dir={triage['direction']}")
        verdict = run_debate(ev.body, triage, ctx)
        print(f"辩论 verdict: score={verdict['score']} dir={verdict['direction']} "
              f"winning={verdict['winning_side']} model={verdict['model']}")
        print(f"  多: {verdict['bull_case'][:100]}")
        print(f"  空: {verdict['bear_case'][:100]}")
        print(f"  判官: {verdict['judge_reasoning'][:140]}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑全量测试确认绿**

Run:`python -m pytest tests/ -v`
Expected: 全部 PASS。

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/eval_debate.py
git commit -m "test(debate): 加 eval_debate 人工评测脚本"
```

---

## Task 13: 全链路核对与收尾

**Files:** 无新增,仅核对。

- [ ] **Step 1: 跑全量测试**

Run:`python -m pytest tests/ -v`
Expected: 全部 PASS(约 28 个用例)。

- [ ] **Step 2: ruff 静态检查**

Run:`python -m ruff check app/services/debate_scorer.py app/services/debate_research.py app/services/debate_queue.py app/services/macro_pusher.py app/workers/jin10_browser_worker.py`
Expected: 无报错(若有 import 排序问题,`ruff check --fix` 后重新 commit)。

- [ ] **Step 3: 确认 db 迁移生效**

Run:`python -c "from app.db import init_db; init_db()"` 然后
`python -c "import sqlite3; print([r[1] for r in sqlite3.connect('data/trading.db').execute('PRAGMA table_info(event_notification)')])"`
Expected: 输出列表里含 `debate_json`。

- [ ] **Step 4: 若有改动则 Commit**

```bash
git add -A
git commit -m "chore(debate): Phase 1 收尾 — lint 与迁移核对"
```

---

## 自检清单(写计划后已核对)

- **Spec 覆盖**:§4 模块划分→Task 4-9;§4.2-4.3 两阶段流/升级判定→Task 4、10、11;
  §5 辩论内核→Task 5-7;§5.1 verdict 超集→Task 7(`_normalize_verdict`);
  §6 异步执行/存储/对账→Task 9;§7 推送→Task 9(`format_debate_push`);
  §9 错误兜底→Task 5(fail-soft)、Task 7(fail-open 回退)、Task 9(僵尸行);
  §10 schema→Task 3;§11 测试→各 Task 的 TDD 步骤 + Task 12;§12 config→Task 2。
  §8 建议侧属 Phase 2,本计划不含 —— 与 spec §13 分期一致。
- **占位符**:无 TBD/TODO;每个代码步骤含完整代码。
- **类型一致**:`run_debate`/`should_escalate`/`build_position_context` 签名跨 Task 一致;
  verdict dict 的 key 集合在 Task 7 定义,Task 9 消费时键名一致;
  `_apply_verdict`/`_triage_from_row` 在 Task 9 内定义并被 Task 9 自身复用。
- **偏离 spec 的小调整**:spec §12 写的 `debate_escalate_score_band: tuple`,本计划落地为
  `debate_escalate_score_lo` + `debate_escalate_score_hi` 两个 int —— pydantic-settings
  从环境变量读 tuple 不便,两个 int 是同一需求的更友好实现。
