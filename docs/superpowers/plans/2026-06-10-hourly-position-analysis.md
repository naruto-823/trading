# 每小时仓位体检 Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每整点(24×7)自动跑一次仓位分析 + 重仓深度调研 + 针对持仓/操作的 AI 指导,落库并 Bark 推一条摘要。

**Architecture:** 新增独立 `position_analysis` service 编排数据流(持仓→重仓筛选→新闻+web_search 调研→Anthropic 出结构化指导→落库→Bark),由独立 APScheduler worker 每整点触发;新增 `position_analysis_report` 表与只读 API。全程 fail-soft:任一步失败降级,绝不整轮崩,降级也照样推一条「降级」摘要。不改动现有 suggestions/debate。

**Tech Stack:** FastAPI + APScheduler(AsyncIOScheduler)+ SQLAlchemy 2.0 + SQLite + Anthropic SDK(原生 messages + web_search_20250305)+ httpx + Bark。

**测试运行:** `cd backend && uv run pytest -v`(单测用内存 SQLite,见 `tests/conftest.py` 的 `db_session` fixture)。

**关键参考实现(照抄模式):**
- AI 调用 / JSON 解析 / 持仓 enrich:`backend/app/services/suggestions.py`(`_call_opus`、`_parse_json`、`_enrich_positions`、`_is_option`)
- web_search 调研:`backend/app/services/debate_research.py`(`gather_research(content, tickers)`)
- worker 模板:`backend/app/workers/suggestions_worker.py`
- 调度注册:`backend/app/workers/scheduler.py`(`record_duration`、`start_scheduler`)
- Bark:`backend/app/services/notify.py`(`send_bark(title, body, *, group, sound, level)`)
- 数据源:`backend/app/services/briefing.py` 的 `fetch_market_context(client)`、`fetch_news_for_symbol(symbol, client, name=, limit=)`
- 持仓/账户:`list_positions(db)` → `list[PositionResponse]`;`get_latest_account(db)` → `AccountSnapshotResponse | None`

---

## Task 1: 配置项

**Files:**
- Modify: `backend/app/config.py`
- Modify: `.env.example`

- [ ] **Step 1: 在 `config.py` 的 `Settings` 类里、`database_url` 之前加配置块**

在 `backend/app/config.py` 中,`# Database` 注释行之前插入:

```python
    # —— 每小时仓位体检 worker (hourly position analysis) ——
    # spec: docs/superpowers/specs/2026-06-10-hourly-position-analysis-design.md
    hourly_analysis_enabled: bool = True
    hourly_analysis_top_n: int = 5            # 监控前 N 大重仓
    hourly_analysis_min_position_pct: float = 5.0   # 占净资产% 阈值,达标才进重仓深调
    hourly_analysis_news_per_stock: int = 3   # 每只重仓拉几条新闻
    hourly_analysis_model: str = ""           # 留空则回退 anthropic_model
    hourly_analysis_websearch_enabled: bool = True

    def hourly_model(self) -> str:
        return self.hourly_analysis_model or self.anthropic_model
```

注意:`hourly_model()` 方法放在 `Settings` 类内、`validate_longport` 方法之前(跟其他 `validate_*` 方法同级)。

- [ ] **Step 2: 在 `.env.example` 追加示例(Bark 配置块之后)**

```bash
# —— 每小时仓位体检 worker ——
HOURLY_ANALYSIS_ENABLED=true
HOURLY_ANALYSIS_TOP_N=5
HOURLY_ANALYSIS_MIN_POSITION_PCT=5.0
HOURLY_ANALYSIS_NEWS_PER_STOCK=3
HOURLY_ANALYSIS_MODEL=
HOURLY_ANALYSIS_WEBSEARCH_ENABLED=true
```

- [ ] **Step 3: 验证配置可加载**

Run: `cd backend && uv run python -c "from app.config import settings; print(settings.hourly_analysis_enabled, settings.hourly_model())"`
Expected: 打印 `True claude-opus-4-7`(或 .env 里实际 anthropic_model)

- [ ] **Step 4: Commit**

```bash
git add backend/app/config.py .env.example
git commit -m "feat(position-analysis): 每小时仓位体检配置项"
```

---

## Task 2: 数据表 `position_analysis_report`

**Files:**
- Create: `backend/app/models/position_analysis_report.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_position_analysis_model.py`

- [ ] **Step 1: 写失败测试**

Create `backend/tests/test_position_analysis_model.py`:

```python
from datetime import datetime

from app.models.position_analysis_report import PositionAnalysisReport


def test_report_persists_and_reads_back(db_session):
    row = PositionAnalysisReport(
        generated_at=datetime.utcnow(),
        account_json='{"net_assets": 100}',
        positions_json='[{"symbol": "MSFT.US"}]',
        research_brief="近期 AI 资本开支上行",
        analysis_json='{"summary": "持"}',
        summary="整体持有,关注 MSFT",
        push_status="sent",
        push_detail="ok",
        degraded=False,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    assert row.id is not None
    got = db_session.get(PositionAnalysisReport, row.id)
    assert got.summary == "整体持有,关注 MSFT"
    assert got.degraded is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_position_analysis_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.position_analysis_report'`

- [ ] **Step 3: 建模型**

Create `backend/app/models/position_analysis_report.py`:

```python
"""每小时仓位体检报告持久化模型

每整点 generate_hourly_analysis 落一行;degraded=True 表示该轮某步降级。
spec: docs/superpowers/specs/2026-06-10-hourly-position-analysis-design.md
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PositionAnalysisReport(Base):
    __tablename__ = "position_analysis_report"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    # 账户快照(净资产/市值/现金/日盈亏 子集)
    account_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 本轮被分析的重仓清单
    positions_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # web_search 调研简报文本(降级时为空)
    research_brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AI 结构化输出:{overall_stance, per_position[], alerts[], summary}
    analysis_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    summary: Mapped[str] = mapped_column(Text, default="")
    push_status: Mapped[str] = mapped_column(String(20), default="pending")  # sent/failed/skipped/pending
    push_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
```

- [ ] **Step 4: 在 `models/__init__.py` 注册模型**

把 `backend/app/models/__init__.py` 改成包含新模型(import 段加一行、`__all__` 加一项):

```python
from app.models.account import AccountSnapshot
from app.models.alert import Alert
from app.models.daily_baseline import DailyBaseline
from app.models.decision import Decision
from app.models.event_notification import EventNotification
from app.models.execution import Execution
from app.models.order import Order
from app.models.position import Position
from app.models.position_analysis_report import PositionAnalysisReport
from app.models.suggestion import Suggestion
from app.models.sync_log import SyncLog

__all__ = [
    "AccountSnapshot", "Alert", "DailyBaseline", "Decision", "EventNotification",
    "Execution", "Order", "Position", "PositionAnalysisReport", "Suggestion", "SyncLog",
]
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_position_analysis_model.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/position_analysis_report.py backend/app/models/__init__.py backend/tests/test_position_analysis_model.py
git commit -m "feat(position-analysis): position_analysis_report 表"
```

---

## Task 3: service 骨架 + 重仓筛选 `select_heavy_positions`

**Files:**
- Create: `backend/app/services/position_analysis.py`
- Test: `backend/tests/test_position_analysis.py`

- [ ] **Step 1: 写失败测试**

Create `backend/tests/test_position_analysis.py`:

```python
from types import SimpleNamespace
from unittest.mock import patch

from app.services import position_analysis as pa


def _pos(symbol, mv, currency="USD"):
    return SimpleNamespace(
        symbol=symbol, name=symbol, quantity=10, cost_price=1.0,
        current_price=1.0, market_value=mv, currency=currency,
        unrealized_pnl=0.0, unrealized_pnl_ratio=0.0, day_pnl_ratio=0.0,
    )


def test_select_heavy_picks_above_threshold_sorted_desc():
    account = SimpleNamespace(net_assets=1000.0)
    positions = [
        _pos("AAA.US", 600), _pos("BBB.US", 300),
        _pos("CCC.US", 50), _pos("DDD.US", 10),
    ]
    # fx 用 identity:HKD 市值 == market_value
    with patch.object(pa.fx_service, "to_hkd", side_effect=lambda v, ccy, db=None: v):
        heavy = pa.select_heavy_positions(positions, account, db=None, top_n=5, min_pct=5.0)
    syms = [p["symbol"] for p in heavy]
    assert syms == ["AAA.US", "BBB.US", "CCC.US"]  # DDD 仅 1% 被剔


def test_select_heavy_excludes_options():
    account = SimpleNamespace(net_assets=1000.0)
    positions = [_pos("AAA.US", 600), _pos("MSFT260627C430000.US", 400)]
    with patch.object(pa.fx_service, "to_hkd", side_effect=lambda v, ccy, db=None: v):
        heavy = pa.select_heavy_positions(positions, account, db=None, top_n=5, min_pct=5.0)
    assert [p["symbol"] for p in heavy] == ["AAA.US"]


def test_select_heavy_fallback_to_top_n_when_none_meet_threshold():
    account = SimpleNamespace(net_assets=100000.0)  # 所有仓位占比都 <5%
    positions = [_pos("AAA.US", 600), _pos("BBB.US", 300), _pos("CCC.US", 50)]
    with patch.object(pa.fx_service, "to_hkd", side_effect=lambda v, ccy, db=None: v):
        heavy = pa.select_heavy_positions(positions, account, db=None, top_n=2, min_pct=5.0)
    assert [p["symbol"] for p in heavy] == ["AAA.US", "BBB.US"]  # 兜底取市值前 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_position_analysis.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.position_analysis'`

- [ ] **Step 3: 写 service 骨架 + `select_heavy_positions`**

Create `backend/app/services/position_analysis.py`:

```python
"""每小时仓位体检服务

数据流:持仓 → 重仓筛选 → 新闻 + web_search 调研 → Anthropic 出结构化指导
       → 落库 position_analysis_report → Bark 推摘要。
全程 fail-soft:任一步异常降级(degraded=True),绝不整轮崩。

spec: docs/superpowers/specs/2026-06-10-hourly-position-analysis-design.md
"""

from __future__ import annotations

import json
import logging
import re

from app.services import fx as fx_service

logger = logging.getLogger(__name__)


def _is_option(symbol: str) -> bool:
    # 期权合约 symbol(如 MSFT260618C440000.US):长 + 含 C/P + 含数字
    return len(symbol) > 12 and any(c in symbol for c in ["C", "P"]) and any(d.isdigit() for d in symbol)


def select_heavy_positions(positions, account, db, top_n: int, min_pct: float) -> list[dict]:
    """选重仓:占净资产% ≥ min_pct 的、按 HKD 市值降序的前 top_n 只(剔除期权)。
    没有任何仓位达标时,兜底取市值前 top_n。返回 enrich 后的 dict 列表。
    """
    net = float(getattr(account, "net_assets", 0) or 0)
    stocks = [p for p in positions if not _is_option(p.symbol)]
    enriched = []
    for p in stocks:
        hkd_mv = fx_service.to_hkd(abs(p.market_value), p.currency, db)
        pct = (hkd_mv / net * 100) if net > 0 else 0.0
        enriched.append({
            "symbol": p.symbol,
            "name": p.name,
            "数量": p.quantity,
            "成本价": p.cost_price,
            "现价": p.current_price,
            "市值": p.market_value,
            "货币": p.currency,
            "占净资产%": round(pct, 1),
            "浮动盈亏": p.unrealized_pnl,
            "浮亏率%": round(p.unrealized_pnl_ratio * 100, 1),
            "当日涨跌%": round(p.day_pnl_ratio * 100, 2),
            "_hkd_mv": hkd_mv,
        })
    enriched.sort(key=lambda d: d["_hkd_mv"], reverse=True)
    heavy = [d for d in enriched if d["占净资产%"] >= min_pct][:top_n]
    if not heavy:
        heavy = enriched[:top_n]  # 兜底:没仓位达标也别空手
    for d in heavy:
        d.pop("_hkd_mv", None)
    return heavy
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_position_analysis.py -v`
Expected: PASS(3 个 test）

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/position_analysis.py backend/tests/test_position_analysis.py
git commit -m "feat(position-analysis): 重仓筛选 select_heavy_positions"
```

---

## Task 4: AI prompt + JSON 解析 `_parse_analysis_json`

**Files:**
- Modify: `backend/app/services/position_analysis.py`
- Test: `backend/tests/test_position_analysis.py`

- [ ] **Step 1: 追加失败测试**

在 `backend/tests/test_position_analysis.py` 末尾追加:

```python
def test_parse_analysis_json_plain():
    raw = '{"overall_stance": "持", "per_position": [], "alerts": ["a"], "summary": "s"}'
    out = pa._parse_analysis_json(raw)
    assert out["summary"] == "s"
    assert out["alerts"] == ["a"]


def test_parse_analysis_json_strips_code_fence():
    raw = '```json\n{"summary": "x", "alerts": [], "per_position": [], "overall_stance": "攻"}\n```'
    out = pa._parse_analysis_json(raw)
    assert out["summary"] == "x"
    assert out["overall_stance"] == "攻"


def test_parse_analysis_json_invalid_returns_degraded():
    out = pa._parse_analysis_json("not json at all")
    assert out["degraded"] is True
    assert "解析失败" in out["summary"]
    assert out["per_position"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_position_analysis.py -k parse_analysis -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_parse_analysis_json'`

- [ ] **Step 3: 加 SYSTEM_PROMPT 与解析函数**

在 `position_analysis.py` 顶部 `logger = ...` 之后插入 prompt 常量:

```python
SYSTEM_PROMPT = """你是用户的**仓位体检官**——每小时给他的持仓做一次盘面体检 + 操作指导。不是风险经理,是随身教练。

【用户画像 —— 必须据此调整语气和结论】
- 自评"损失厌恶 + 易补仓 + 跑不赢纳指"。点名他的补仓冲动,但**不要无脑劝阻**;给的是纪律,不是恐吓。
- mega-cap 长仓(MSFT/GOOG/META/NVDA 等)是他的赚钱机器:用**前瞻视角 + 按方向加权**判断,**别太保守、别滞后、别反射性劝降风险**。趋势没破坏就别喊减仓。
- 偏好期权 income 策略:指导里带 covered call / cash-secured put 视角。
  **硬护栏:covered call 只能在该正股持仓 ≥100 股时提;不足 100 股或没持有,禁止建议 covered call。**
- 两可决策给出你的**独立判断**(不要"看个人风险偏好"和稀泥);纯防御性问题直接给执行动作。

【输入】账户概览 + 重仓清单(含成本/现价/占比/浮亏率/当日涨跌)+ 重仓近期新闻标题 + web_search 研究简报 + 市场背景。

【输出】严格 JSON(不要 markdown 包裹),schema:
{
  "overall_stance": "攻 | 守 | 持 —— 后跟一句话理由",
  "per_position": [
    {"symbol": "MSFT.US", "read": "1-2 句盘面解读(基于输入数据/新闻/调研)", "guidance": "具体操作指导(持/加/减/写 covered call/对冲...)", "signal": "强 | 中 | 弱"}
  ],
  "alerts": ["需要你特别注意的点,按重要度排序,可为空数组"],
  "summary": "一句话中文摘要(整体盘面 + 最关键的一个动作),≤60 字"
}

【硬规则】
1. per_position 覆盖输入的每只重仓,不要漏。
2. read / guidance 必须基于输入里的真实数据(占比、浮亏率、新闻标题、调研简报、市场背景),**不要编股价、财报日期、市占率等输入里没有的硬事实**。
3. 不要"持有观察""关注 XX 价位"这种没信息量的空话;guidance 要可执行。
4. covered call 建议严守 ≥100 股护栏(见上)。
5. summary 是要推到他手机锁屏的那句话,务必精炼、有判断、有动作。"""


def _parse_analysis_json(text: str) -> dict:
    """解析 AI 输出 JSON;失败返回 degraded 降级结构。"""
    text = (text or "").strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl > 0:
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        data = json.loads(text)
        data.setdefault("overall_stance", "")
        data.setdefault("per_position", [])
        data.setdefault("alerts", [])
        data.setdefault("summary", "")
        return data
    except json.JSONDecodeError as exc:
        logger.warning("position-analysis JSON parse 失败: %s | raw: %s", exc, text[:200])
        return {
            "overall_stance": "",
            "per_position": [],
            "alerts": ["AI 输出解析失败"],
            "summary": "⚠️ 本轮体检解析失败",
            "degraded": True,
        }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_position_analysis.py -k parse_analysis -v`
Expected: PASS(3 个)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/position_analysis.py backend/tests/test_position_analysis.py
git commit -m "feat(position-analysis): 体检官 prompt + JSON 解析"
```

---

## Task 5: AI 调用 `_call_ai`

**Files:**
- Modify: `backend/app/services/position_analysis.py`
- Test: `backend/tests/test_position_analysis.py`

- [ ] **Step 1: 追加失败测试(验证 fail-soft + payload 不崩)**

在 `backend/tests/test_position_analysis.py` 末尾追加:

```python
def test_call_ai_fail_soft_on_exception():
    account = SimpleNamespace(net_assets=1000.0, market_value=900.0,
                             total_cash=100.0, day_pnl=5.0, buy_power=200.0)
    heavy = [{"symbol": "AAA.US", "占净资产%": 60.0}]
    # 让 Anthropic client 构造即抛 → 命中 except 分支
    with patch.object(pa, "Anthropic", side_effect=RuntimeError("boom")):
        out = pa._call_ai(account, heavy, market_ctx={}, news_by_symbol={}, research="")
    assert out["degraded"] is True
    assert "降级" in out["summary"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_position_analysis.py -k call_ai -v`
Expected: FAIL — `AttributeError: ... has no attribute 'Anthropic'`(或 `_call_ai`)

- [ ] **Step 3: 加 import 与 `_call_ai`**

在 `position_analysis.py` 的 import 段补上(`from app.services import fx as fx_service` 附近):

```python
from datetime import datetime

from anthropic import Anthropic

from app.config import settings
```

然后在文件末尾追加:

```python
def _call_ai(account, heavy_positions, market_ctx, news_by_symbol, research) -> dict:
    """调 Anthropic 原生通道出体检 JSON。fail-soft:任何异常 → degraded 降级结构。"""
    if not settings.anthropic_api_key:
        return _degraded("AI 未配置")
    payload = {
        "现在时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "账户概览": {
            "净资产_HKD": getattr(account, "net_assets", None),
            "总市值_HKD": getattr(account, "market_value", None),
            "现金_HKD": getattr(account, "total_cash", None),
            "当日盈亏_HKD": getattr(account, "day_pnl", None),
            "购买力_HKD": getattr(account, "buy_power", None),
        },
        "重仓清单": heavy_positions,
        "重仓近期新闻标题": {
            sym: [n.get("title", "") for n in news] for sym, news in news_by_symbol.items()
        },
        "web_search研究简报": research or "(本轮无外部调研)",
        "市场背景": market_ctx,
    }
    try:
        client = Anthropic(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url or None,
        )
        resp = client.messages.create(
            model=settings.hourly_model(),
            max_tokens=3000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2, default=str)}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text") or "{}"
        return _parse_analysis_json(text)
    except Exception as exc:
        logger.error("position-analysis _call_ai 失败: %s", exc, exc_info=True)
        return _degraded(f"AI 调用失败: {exc}")


def _degraded(reason: str) -> dict:
    return {
        "overall_stance": "",
        "per_position": [],
        "alerts": [reason],
        "summary": f"⚠️ 本轮仓位体检降级({reason})",
        "degraded": True,
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_position_analysis.py -k call_ai -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/position_analysis.py backend/tests/test_position_analysis.py
git commit -m "feat(position-analysis): _call_ai Anthropic 调用 + fail-soft 降级"
```

---

## Task 6: Bark 推送文案 `_build_push`

**Files:**
- Modify: `backend/app/services/position_analysis.py`
- Test: `backend/tests/test_position_analysis.py`

- [ ] **Step 1: 追加失败测试**

在 `backend/tests/test_position_analysis.py` 末尾追加:

```python
def test_build_push_contains_assets_summary_and_alerts():
    account = SimpleNamespace(net_assets=1234567.0, day_pnl=-8900.0)
    analysis = {"summary": "整体持有,MSFT 趋势完好", "alerts": ["NVDA 财报临近", "GOOG 反垄断进展"]}
    title, body = pa._build_push(analysis, account)
    assert "仓位体检" in title
    assert "1,234,567" in title          # 净资产带千分位
    assert "整体持有" in body             # summary 进正文
    assert "NVDA 财报临近" in body         # alert 前两条进正文
    assert "GOOG 反垄断进展" in body
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_position_analysis.py -k build_push -v`
Expected: FAIL — `AttributeError: ... has no attribute '_build_push'`

- [ ] **Step 3: 加 `_build_push`**

在 `position_analysis.py` 末尾追加:

```python
def _build_push(analysis: dict, account) -> tuple[str, str]:
    """生成 Bark 标题 + 正文。每整点都推一条摘要。"""
    net = float(getattr(account, "net_assets", 0) or 0)
    day_pnl = float(getattr(account, "day_pnl", 0) or 0)
    sign = "+" if day_pnl >= 0 else ""
    title = f"📊 仓位体检 · 净资产HK${net:,.0f} 日{sign}{day_pnl:,.0f}"
    lines = [analysis.get("summary", "") or "(本轮无摘要)"]
    for a in (analysis.get("alerts") or [])[:2]:
        lines.append(f"• {a}")
    body = "\n".join(lines)[:600]
    return title, body
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_position_analysis.py -k build_push -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/position_analysis.py backend/tests/test_position_analysis.py
git commit -m "feat(position-analysis): Bark 推送文案 _build_push"
```

---

## Task 7: 编排 `generate_hourly_analysis` + 读取函数

**Files:**
- Modify: `backend/app/services/position_analysis.py`
- Test: `backend/tests/test_position_analysis.py`

- [ ] **Step 1: 追加失败测试(全链路 fail-soft + 落库 + 推送 + 读取)**

在 `backend/tests/test_position_analysis.py` 末尾追加:

```python
from app.models.position_analysis_report import PositionAnalysisReport


def test_generate_persists_report_and_pushes_even_when_research_fails(db_session):
    account = SimpleNamespace(net_assets=1000.0, market_value=900.0,
                             total_cash=100.0, day_pnl=5.0, buy_power=200.0)
    positions = [_pos("AAA.US", 600)]
    analysis = {"overall_stance": "持", "per_position": [],
                "alerts": ["x"], "summary": "持有 AAA"}

    with patch.object(pa, "list_positions", return_value=positions), \
         patch.object(pa, "get_latest_account", return_value=account), \
         patch.object(pa.fx_service, "to_hkd", side_effect=lambda v, ccy, db=None: v), \
         patch.object(pa, "_collect_market_data", side_effect=RuntimeError("news down")), \
         patch.object(pa, "gather_research", side_effect=RuntimeError("ws down")), \
         patch.object(pa, "_call_ai", return_value=analysis), \
         patch.object(pa, "send_bark", return_value={"ok": True, "detail": "ok"}) as mock_bark:
        out = pa.generate_hourly_analysis(db_session)

    # 报告落库
    rows = db_session.query(PositionAnalysisReport).all()
    assert len(rows) == 1
    assert rows[0].research_brief == ""        # 调研失败 → 空,但不崩
    assert rows[0].push_status == "sent"
    mock_bark.assert_called_once()
    assert out["summary"] == "持有 AAA"


def test_generate_no_positions_pushes_degraded(db_session):
    with patch.object(pa, "list_positions", return_value=[]), \
         patch.object(pa, "get_latest_account", return_value=None), \
         patch.object(pa, "send_bark", return_value={"ok": True, "detail": "ok"}) as mock_bark:
        out = pa.generate_hourly_analysis(db_session)
    rows = db_session.query(PositionAnalysisReport).all()
    assert len(rows) == 1
    assert rows[0].degraded is True
    mock_bark.assert_called_once()
    assert "暂无" in out["summary"]


def test_get_latest_report_returns_most_recent(db_session):
    from datetime import datetime, timedelta
    old = PositionAnalysisReport(generated_at=datetime.utcnow() - timedelta(hours=1), summary="旧")
    new = PositionAnalysisReport(generated_at=datetime.utcnow(), summary="新")
    db_session.add_all([old, new])
    db_session.commit()
    got = pa.get_latest_report(db_session)
    assert got["summary"] == "新"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_position_analysis.py -k "generate or latest_report" -v`
Expected: FAIL — `AttributeError: ... has no attribute 'generate_hourly_analysis'`

- [ ] **Step 3: 加 import、`_collect_market_data`、编排与读取函数**

在 `position_analysis.py` import 段补上:

```python
import httpx
from sqlalchemy.orm import Session

from app.models.position_analysis_report import PositionAnalysisReport
from app.services.account import get_latest_account
from app.services.briefing import HTTP_HEADERS, fetch_market_context, fetch_news_for_symbol
from app.services.debate_research import gather_research
from app.services.notify import send_bark
from app.services.positions import list_positions
```

在文件末尾追加:

```python
def _collect_market_data(heavy_positions: list[dict]) -> tuple[dict, dict]:
    """拉市场背景 + 重仓新闻。整体包在外层 try 里(调用方负责降级)。"""
    with httpx.Client(timeout=10.0, headers=HTTP_HEADERS, follow_redirects=True) as client:
        market_ctx = fetch_market_context(client)
        news_by_symbol = {
            p["symbol"]: fetch_news_for_symbol(
                p["symbol"], client, name=p.get("name"),
                limit=settings.hourly_analysis_news_per_stock,
            )
            for p in heavy_positions
        }
    return market_ctx, news_by_symbol


def generate_hourly_analysis(db: Session) -> dict:
    """每整点编排:持仓→重仓→调研→AI→落库→Bark。全程 fail-soft。"""
    generated_at = datetime.utcnow()
    positions = list_positions(db)
    account = get_latest_account(db)

    if not positions or not account:
        analysis = _degraded("暂无持仓/账户数据,请先同步")
        return _persist_and_push(db, generated_at, account, [], "", analysis, degraded=True)

    heavy = select_heavy_positions(
        positions, account, db,
        top_n=settings.hourly_analysis_top_n,
        min_pct=settings.hourly_analysis_min_position_pct,
    )

    # 市场数据(fail-soft)
    market_ctx, news_by_symbol = {}, {}
    try:
        market_ctx, news_by_symbol = _collect_market_data(heavy)
    except Exception as exc:
        logger.warning("position-analysis 市场数据降级: %s", exc)

    # web_search 调研(fail-soft;gather_research 自身永不抛,这里再兜一层)
    research = ""
    if settings.hourly_analysis_websearch_enabled:
        try:
            tickers = [p["symbol"] for p in heavy]
            content = "组合重仓体检:" + ", ".join(tickers)
            research = gather_research(content, tickers)
        except Exception as exc:
            logger.warning("position-analysis 调研降级: %s", exc)
            research = ""

    analysis = _call_ai(account, heavy, market_ctx, news_by_symbol, research)
    degraded = bool(analysis.get("degraded"))
    return _persist_and_push(db, generated_at, account, heavy, research, analysis, degraded=degraded)


def _persist_and_push(db, generated_at, account, heavy, research, analysis, degraded) -> dict:
    """落库 + Bark 推送(每整点都推)。返回报告 dict。"""
    account_json = None
    if account is not None:
        account_json = json.dumps({
            "net_assets": getattr(account, "net_assets", None),
            "market_value": getattr(account, "market_value", None),
            "total_cash": getattr(account, "total_cash", None),
            "day_pnl": getattr(account, "day_pnl", None),
        }, ensure_ascii=False)

    row = PositionAnalysisReport(
        generated_at=generated_at,
        account_json=account_json,
        positions_json=json.dumps(heavy, ensure_ascii=False, default=str),
        research_brief=research or "",
        analysis_json=json.dumps(analysis, ensure_ascii=False),
        summary=analysis.get("summary", ""),
        push_status="pending",
        degraded=degraded,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # 推送(account 为 None 时给个占位,_build_push 用 getattr 安全)
    title, body = _build_push(analysis, account)
    res = send_bark(title, body, group="position-analysis", level="active")
    row.push_status = "sent" if res.get("ok") else "failed"
    row.push_detail = str(res.get("detail"))[:500]
    db.commit()
    db.refresh(row)

    return _row_to_dict(row)


def _row_to_dict(row: PositionAnalysisReport) -> dict:
    return {
        "id": row.id,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
        "account": json.loads(row.account_json) if row.account_json else None,
        "positions": json.loads(row.positions_json) if row.positions_json else [],
        "research_brief": row.research_brief or "",
        "analysis": json.loads(row.analysis_json) if row.analysis_json else {},
        "summary": row.summary,
        "push_status": row.push_status,
        "degraded": row.degraded,
    }


def get_latest_report(db: Session) -> dict | None:
    row = (
        db.query(PositionAnalysisReport)
        .order_by(PositionAnalysisReport.generated_at.desc())
        .first()
    )
    return _row_to_dict(row) if row else None


def list_report_history(db: Session, limit: int = 24) -> list[dict]:
    rows = (
        db.query(PositionAnalysisReport)
        .order_by(PositionAnalysisReport.generated_at.desc())
        .limit(limit)
        .all()
    )
    return [_row_to_dict(r) for r in rows]
```

注意:`_build_push` 里所有字段都用 `getattr(account, ..., 0)`,所以 `account=None` 时(无持仓分支)`getattr(None, "net_assets", 0)` 返回 0,标题显示 `净资产HK$0`,不崩。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_position_analysis.py -v`
Expected: PASS(全部,含 generate / latest_report）

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/position_analysis.py backend/tests/test_position_analysis.py
git commit -m "feat(position-analysis): generate_hourly_analysis 编排 + 读取函数"
```

---

## Task 8: Worker + scheduler 注册

**Files:**
- Create: `backend/app/workers/hourly_position_analysis_worker.py`
- Modify: `backend/app/workers/scheduler.py`
- Test: `backend/tests/test_hourly_position_analysis_worker.py`

- [ ] **Step 1: 写失败测试**

Create `backend/tests/test_hourly_position_analysis_worker.py`:

```python
from unittest.mock import patch

from apscheduler.schedulers.background import BackgroundScheduler

from app.workers import hourly_position_analysis_worker as w


def test_register_adds_job_when_enabled():
    sched = BackgroundScheduler(timezone="UTC")
    with patch.object(w.settings, "hourly_analysis_enabled", True):
        w.register(sched)
    assert sched.get_job(w.JOB_ID) is not None


def test_register_skips_job_when_disabled():
    sched = BackgroundScheduler(timezone="UTC")
    with patch.object(w.settings, "hourly_analysis_enabled", False):
        w.register(sched)
    assert sched.get_job(w.JOB_ID) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_hourly_position_analysis_worker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.workers.hourly_position_analysis_worker'`

- [ ] **Step 3: 建 worker**

Create `backend/app/workers/hourly_position_analysis_worker.py`:

```python
"""每小时仓位体检 worker —— 每整点(24×7)跑一次

generate_hourly_analysis 含新闻抓取 + web_search + Anthropic 调用,耗时数十秒,
用 run_in_threadpool 避免阻塞 event loop。受 settings.hourly_analysis_enabled 开关控制。

spec: docs/superpowers/specs/2026-06-10-hourly-position-analysis-design.md
"""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi.concurrency import run_in_threadpool

from app.config import settings
from app.db import SessionLocal
from app.services.position_analysis import generate_hourly_analysis
from app.workers.scheduler import record_duration

logger = logging.getLogger(__name__)

JOB_ID = "hourly-position-analysis"


def _run_once_sync() -> dict:
    db = SessionLocal()
    try:
        return generate_hourly_analysis(db)
    finally:
        db.close()


async def run_hourly_analysis_job() -> None:
    t0 = time.time()
    try:
        result = await run_in_threadpool(_run_once_sync)
        logger.info(
            "hourly-position-analysis: 已生成报告 push=%s degraded=%s",
            result.get("push_status"), result.get("degraded"),
        )
    except Exception as exc:
        logger.error("hourly-position-analysis failed: %s", exc, exc_info=True)
    finally:
        record_duration(JOB_ID, int((time.time() - t0) * 1000))


def register(sched: BaseScheduler) -> None:
    """每整点(minute=0)跑一次。enabled=False 时不挂 job。"""
    if not settings.hourly_analysis_enabled:
        logger.info("hourly-position-analysis 已禁用 (HOURLY_ANALYSIS_ENABLED=false),跳过注册")
        return
    sched.add_job(
        run_hourly_analysis_job,
        trigger=CronTrigger(minute=0, timezone="UTC"),
        id=JOB_ID,
        name="每小时仓位体检(分析+调研+指导)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
```

注:测试用 `BackgroundScheduler` 仅为拿到 `add_job` 行为(不 start);生产用 `AsyncIOScheduler`。两者都是 `BaseScheduler` 子类,`register` 形参标注 `BaseScheduler` 兼容。

- [ ] **Step 4: 在 `scheduler.py` 注册**

修改 `backend/app/workers/scheduler.py` 的 `start_scheduler()`:延迟 import 段(现有 `from app.workers.suggestions_worker import ...` 之后)加一行,并在 `register_suggestions(sched)` 之后加调用。

import 段追加:
```python
    from app.workers.hourly_position_analysis_worker import register as register_hourly_analysis
```

注册调用追加(`register_suggestions(sched)` 之后):
```python
    register_hourly_analysis(sched)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_hourly_position_analysis_worker.py -v`
Expected: PASS(2 个)

- [ ] **Step 6: Commit**

```bash
git add backend/app/workers/hourly_position_analysis_worker.py backend/app/workers/scheduler.py backend/tests/test_hourly_position_analysis_worker.py
git commit -m "feat(position-analysis): 每小时 worker + scheduler 注册"
```

---

## Task 9: 只读 API + main.py 注册

**Files:**
- Create: `backend/app/api/position_analysis.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_position_analysis_api.py`

- [ ] **Step 1: 写失败测试(用 TestClient + 覆盖 get_db 依赖)**

Create `backend/tests/test_position_analysis_api.py`:

```python
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db import Base, get_db
from app.main import app
from app.models.position_analysis_report import PositionAnalysisReport


def _client_with_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), TestingSession


def test_latest_endpoint_returns_report():
    client, TestingSession = _client_with_db()
    db = TestingSession()
    db.add(PositionAnalysisReport(generated_at=datetime.utcnow(), summary="最新体检"))
    db.commit()
    db.close()
    try:
        resp = client.get("/api/position-analysis/latest")
        assert resp.status_code == 200
        assert resp.json()["data"]["summary"] == "最新体检"
    finally:
        app.dependency_overrides.clear()


def test_latest_endpoint_empty_returns_null_data():
    client, _ = _client_with_db()
    try:
        resp = client.get("/api/position-analysis/latest")
        assert resp.status_code == 200
        assert resp.json()["data"] is None
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_position_analysis_api.py -v`
Expected: FAIL — 404(路由还没注册)

- [ ] **Step 3: 建 API router**

Create `backend/app/api/position_analysis.py`:

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.position_analysis import get_latest_report, list_report_history
from app.workers.scheduler import trigger_job_now

router = APIRouter()


@router.get("/position-analysis/latest")
def get_latest(db: Session = Depends(get_db)):
    return {"data": get_latest_report(db), "error": None}


@router.get("/position-analysis/history")
def get_history(
    limit: int = Query(24, ge=1, le=200, description="返回最近 N 条"),
    db: Session = Depends(get_db),
):
    return {"data": list_report_history(db, limit=limit), "error": None}


@router.post("/position-analysis/run-now")
async def run_now():
    """立即触发一次体检(复用 scheduler 的 trigger_job_now)。"""
    ok = await trigger_job_now("hourly-position-analysis")
    return {"data": {"triggered": ok}, "error": None if ok else "job 未注册(可能已禁用)"}
```

- [ ] **Step 4: 在 `main.py` 注册路由**

修改 `backend/app/main.py` 第 55 行的 import,把 `position_analysis` 加进去(按字母序插在 `options,` 之后):

```python
from app.api import account, alerts, briefing, chat, decisions, events, health, options, position_analysis, quotes, suggestions, sync, system, trades, ws  # noqa: E402
```

在 `app.include_router(events.router, ...)` 之后追加一行:

```python
app.include_router(position_analysis.router, prefix="/api", tags=["position-analysis"])
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_position_analysis_api.py -v`
Expected: PASS(2 个)

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/position_analysis.py backend/app/main.py backend/tests/test_position_analysis_api.py
git commit -m "feat(position-analysis): 只读 API (latest/history/run-now) + 路由注册"
```

---

## Task 10: 全量回归 + 手动冒烟

**Files:** 无新增

- [ ] **Step 1: 跑全量测试**

Run: `cd backend && uv run pytest -v`
Expected: 全绿。重点确认未弄坏 `test_suggestions*`、`test_debate*`、`test_config`。

- [ ] **Step 2: Lint(若项目有)**

Run: `cd backend && uv run ruff check app/services/position_analysis.py app/workers/hourly_position_analysis_worker.py app/api/position_analysis.py app/models/position_analysis_report.py 2>/dev/null || echo "ruff 未配置,跳过"`
Expected: 无 error(或跳过)

- [ ] **Step 3: 冒烟——手动触发一次真实跑(需 .env 配好 anthropic + bark + longport)**

启动后端:`cd backend && uv run uvicorn app.main:app`(另开终端),然后:
Run: `curl -s -X POST http://127.0.0.1:8000/api/position-analysis/run-now`
Expected: `{"data":{"triggered":true},"error":null}`,几十秒后手机收到一条「📊 仓位体检」Bark,且 `curl -s http://127.0.0.1:8000/api/position-analysis/latest` 返回最新报告。

> 注:冒烟需真实凭证,自动化测试不覆盖此步;若 .env 未配齐,跳过 Step 3,以单测绿为准。

- [ ] **Step 4: 最终 Commit(若 Step 1-2 有微调)**

```bash
git add -A
git commit -m "test(position-analysis): 全量回归通过"
```

---

## Self-Review 记录

- **Spec 覆盖:** 运行时段(Task 8 CronTrigger minute=0,24×7)/ 每小时推摘要(Task 7 `_persist_and_push` 无条件 send_bark)/ 重仓聚焦(Task 3 `select_heavy_positions`)/ 独立 worker+表(Task 2/8)/ fail-soft(Task 5/7)/ 读取接口+run-now(Task 9)—— 全部有对应任务。
- **类型一致性:** `select_heavy_positions(positions, account, db, top_n, min_pct)`、`_parse_analysis_json(text)`、`_call_ai(account, heavy_positions, market_ctx, news_by_symbol, research)`、`_build_push(analysis, account)`、`generate_hourly_analysis(db)`、`get_latest_report(db)`、`list_report_history(db, limit)`、worker `JOB_ID="hourly-position-analysis"` —— 跨任务引用一致。
- **无占位:** 所有 step 含完整代码/命令/期望输出。
- **YAGNI:** 不做前端页面、不做全持仓深调、不改 suggestions/debate。
