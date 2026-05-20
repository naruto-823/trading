# 辩论评分引擎 Phase 2 设计 —— 建议引擎接入辩论复核

- 日期: 2026-05-20
- 状态: 已确认,待转 implementation plan
- 作者: naruto + Claude
- 前置: Phase 1 已实现(`docs/superpowers/specs/2026-05-20-debate-scorer-design.md`、
  `docs/superpowers/plans/2026-05-20-debate-scorer-phase1.md`),`debate_scorer.run_debate` /
  `build_position_context` 已建好且测试覆盖。

## 1. 背景

`services/suggestions.py` 的 `build_suggestions` 每次跑一个 Opus 批量调用,产出 5-8 条
可执行建议(action/symbol/qty/price/urgency/thesis/data_points)。`urgency` 本来就是
**Opus 自己给的**(SYSTEM_PROMPT 里定义 high/medium/low),不是静态规则。

问题:单次 Opus 判断没有对抗复核 —— 典型案例 INTW(2x 杠杆 INTC ETF)连续被建议
「卖出 / urgency=high」,而它当时正处于 +13% 反弹途中。Phase 2 在 Opus 批量产出之后,
加一道**看多/看空辩论的对抗复核**,用判官 verdict 调整每条建议。

## 2. 目标 / 非目标

**目标**
- Opus 批量产出后,对每个候选标的跑 Phase 1 的 `run_debate` 做第二意见复核。
- verdict 与建议矛盾时:**标注 + 降级 urgency,不删不改动作** —— 把两可决策摆给用户。
- 复用 Phase 1 的 `run_debate` / `build_position_context`,不重写辩论内核。
- 全链路 fail-open:辩论失败绝不丢 Opus 建议。

**非目标**
- 不重写 `_call_opus`(它仍负责挖掘机会、产候选批次)。
- 不做前端流式「辩论中」状态 —— 改 worker 预生成模型后用户永远看到已复核的完整批次。
- 不引 migration 框架。
- 不加新 config —— 复用 Phase 1 的 `debate_*`。

## 3. 已锁定的决策

| 项 | 决定 |
|---|---|
| 矛盾处理 | 标注 + 降级 urgency,**不删不改动作**(符合 [[feedback_ai_suggestions_judgment]]:两可给用户独立判断) |
| 触发节奏 | 定时后台预生成 —— 新增 worker 定时跑,按需 API 只读最新批次不内联重算 |
| 调度频率 | 每天 2 次(美股盘前 + 收盘后) |
| 架构 | 方案 A —— 独立 `suggestion_debate.py` 模块,`suggestions.py` 只多一行调用 |
| 辩论输入 | 中性合成 content(不喂建议动作/thesis,要独立第二意见) |

## 4. 架构

### 4.1 模块划分

| 文件 | 角色 |
|---|---|
| `services/suggestion_debate.py` | **新建** —— `debate_batch(suggestions, db)`:对每条建议跑辩论复核,原地改 urgency/thesis/debate_json |
| `workers/suggestions_worker.py` | **新建** —— 每天 2 次定时跑 `build_suggestions(force_refresh=True)` |
| `services/suggestions.py` | 改:`_check_affordability` 后加 `debate_batch` 调用;`force_refresh=False` 改成只返回最新批次 |
| `models/suggestion.py` | 改:加 `debate_json TEXT` 列 |
| `db.py` | 改:轻量迁移加 `("suggestion", "debate_json", "TEXT")` |
| `workers/scheduler.py` | 改:注册 suggestions_worker |

设计原则:`suggestion_debate` 是 in-place 后处理(跟 `_verify_prices`/`_check_affordability`
同模式);不认识 worker,只吃 suggestions list + db。

### 4.2 数据流

```
suggestions_worker(每天 2 次:13:00 UTC 盘前 / 22:00 UTC 收盘后)
  → build_suggestions(db, force_refresh=True)
       ① _call_opus 产出 5-8 条建议(不变)
       ② _verify_prices / _check_affordability(不变)
       ③ ★ suggestion_debate.debate_batch(suggestions, db) ★ 新增
       ④ _persist_batch(已含辩论结果)
按需 API(force_refresh=False):只返回 worker 产出的最新批次,不内联重算
手动刷新(force_refresh=True):显式触发完整重算(用户接受数分钟等待)
```

## 5. `debate_batch` —— 辩论输入合成

```
对 suggestions 里每条建议:
  期权合约 symbol(_is_option)→ 跳过,不辩论
收集唯一的非期权 symbol → 每个跑一次 run_debate(并行,debate_max_workers 线程池;
  同 symbol 在同一批次内只辩一次,结果缓存复用)
```

`debate_enabled=False` → `debate_batch` 直接 no-op。

喂给 `run_debate(content, triage, position_ctx)` 的三个参数:

- **content**(中性 —— 故意不喂建议的动作/thesis,要独立第二意见):
  `"复核:此刻应该看多还是看空 {symbol}({name})?结合该标的近况与用户持仓给方向判断。"`
- **triage**(合成):
  `{"relevance": "direct", "score": 60, "sentiment": "neutral", "direction": "neutral", "confidence": 50, "affected_tickers": [symbol], "reason": "建议复核", "model": "suggestion"}`
  —— 驱动 `gather_research` / `build_position_context`;辩论全挂时它也是 fail-open 回退值。
  `model` 非 `"fail-open"`,正常参与辩论。
- **position_ctx**:`build_position_context([symbol])`

## 6. verdict → 调整每条建议

### 6.1 一致性分类

动作隐含方向:`buy`/`add` → 看多(bullish);`sell`/`stop_loss` → 看空(bearish)。

```
若 verdict.model == "debate-degraded"
   或 verdict.winning_side == "balanced"
   或 verdict.direction == "neutral":
    consistency = "mixed"
elif verdict.direction == 动作隐含方向:
    consistency = "agree"
else:
    consistency = "contradict"
```

### 6.2 调整(标注 + 降级,不删不改动作)

urgency 档位:`high > medium > low`,降一档 = high→medium→low(low 保持 low)。

| consistency | urgency | thesis 追加一行 |
|---|---|---|
| `agree` | 保持 Opus 原值 | `⚖️ 辩论复核:判官同向({winning_side},判官 {confidence}%)— {judge_reasoning}` |
| `contradict` | **降一档** | `⚖️ 辩论复核:判官倾向看{涨/跌},与本动作相左 —— {反方 case}。两可,你来定。` |
| `mixed` | **降一档** | `⚖️ 辩论复核:多空僵持/存疑 — {judge_reasoning}` |

(追加行里的引用文本各自截断到合理长度;`{反方 case}` = contradict 时与建议动作相反那一方的
case:卖建议被判看涨 → 引 `bull_case`;买建议被判看跌 → 引 `bear_case`。)

### 6.3 存储

每条被辩论的建议(option symbol 除外)写入 `debate_json`:
```jsonc
{
  "direction": "bullish|bearish|neutral",
  "winning_side": "bull|bear|balanced",
  "confidence": 0-100,
  "consistency": "agree|contradict|mixed",
  "bull_case": "...",
  "bear_case": "...",
  "judge_reasoning": "..."
}
```
`urgency` 用调整后的值;`thesis` = Opus 原文 + §6.2 追加行。期权 symbol 的建议 `debate_json`
保持 `None`、`urgency`/`thesis` 不变。

## 7. Schema 改动

- `suggestion` 模型加 `debate_json: Mapped[str | None] = mapped_column(Text, nullable=True)`
- `db.py` 的 `_apply_lightweight_migrations` 加 `("suggestion", "debate_json", "TEXT")`
- `_persist_batch` 写入 `debate_json`(从 `s.get("debate_json")`,dict → JSON string)
- `_row_to_dict` 响应加 `"debate"` 字段(parsed debate_json,无则 `None`)

## 8. `build_suggestions` 改动

1. `_check_affordability(...)` 之后插入(包 try/except —— 辩论失败也不丢 Opus 建议):
   ```python
   try:
       suggestion_debate.debate_batch(result.get("suggestions", []), db)
   except Exception as exc:
       logger.warning("debate_batch 失败,落库未复核批次: %s", exc)
   ```
2. `force_refresh=False` 改成「只返回最新批次,绝不内联重算」:
   - 有批次 → 直接返回(`cache_hit=True`,不看年龄)
   - 无批次 → `_empty_response("建议尚未生成,等下次定时刷新或手动刷新")`
   - `force_refresh=True`(worker / 用户手动刷新)→ 走完整重算
3. 删除 `CACHE_TTL_SECONDS` 常量及年龄判断 —— freshness 由 worker 负责。

## 9. Worker `suggestions_worker.py`

- `CronTrigger(hour="13,22", minute=0, timezone="UTC")` —— 13:00 UTC ≈ 美股盘前、
  22:00 UTC ≈ 收盘后(对 EDT/EST 都落在盘前/盘后时段,DST 容错)。
- 每次:`run_in_threadpool` 跑 `build_suggestions(db, force_refresh=True)`(含辩论,
  耗时数分钟,后台跑无所谓)。
- `register(sched)`,在 `scheduler.py` 注册(跟 Phase 1 加 overnight_quote/daily_baseline
  同样式)。
- `max_instances=1, coalesce=True, misfire_grace_time=600`,job 失败只 log 不崩调度器。

## 10. 错误兜底(与 Phase 1 fail-open 一致)

| 失败点 | 行为 |
|---|---|
| 单 symbol 的 `run_debate` 抛异常 | 该 symbol 的建议不加辩论标注(debate_json=None,urgency 不变),其他建议照常 |
| `run_debate` 内部失败 | Phase 1 已 fail-open(永远返回 verdict);degraded verdict → consistency=mixed |
| 整个 `debate_batch` 失败 | `build_suggestions` 兜住,落库未复核的 Opus 批次 —— **绝不因辩论丢建议** |
| `debate_enabled=False` | `debate_batch` no-op,建议不复核 |
| worker job 抛异常 | log,不崩调度器 |

## 11. 测试策略(按项目 TDD 规范)

- `test_suggestion_debate.py`:
  - 一致性三分类 —— 表驱动(action × verdict.direction × winning_side → agree/contradict/mixed)。
  - urgency 降档映射(high→medium、medium→low、low→low)。
  - 三类 thesis 标注文本。
  - `debate_batch`(mock `run_debate`):建议拿到 `debate_json` + 调整后 urgency;期权 symbol 跳过;
    同 symbol 只辩一次(per-symbol 缓存);单 symbol `run_debate` 抛异常 → 该建议不崩、整批存活。
  - `debate_enabled=False` → no-op。
- `test_suggestions.py`:`build_suggestions(force_refresh=False)` 有批次 → 返回不重算
  (mock `_call_opus` 断言未被调用);无批次 → 空响应。
- `suggestion.debate_json` 落库往返测试。
- `test_suggestions_worker.py`:`register` 挂上 job;job 跑通(mock `build_suggestions`)。

## 12. Config

不加新 config。复用 Phase 1 的 `debate_*`:`debate_enabled`(关掉则 `debate_batch` no-op)、
`debate_max_workers`(复用作 `debate_batch` 的并行池)、`debate_bull/bear/judge_model` 等
(经 `run_debate` 间接用)。worker 调度时间硬编在 worker 里(跟其他 worker 一致)。

## 13. 已知限制

- 部署后到首次 13:00/22:00 UTC 之间,建议页为空(显示「建议尚未生成」)。可手动刷新触发。
  不在 app 启动时自动跑(省钱)。
- `debate_json` 已落库并进 API 响应;前端是否单独渲染「辩论」面板是可选增强 —— urgency 变化 +
  thesis 追加行已让辩论影响零前端改动即可见。(与 Phase 1 §1 的 dashboard 遗留同性质。)
