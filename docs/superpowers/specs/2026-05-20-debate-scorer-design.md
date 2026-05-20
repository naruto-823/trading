# 辩论评分引擎设计 (Debate Scoring Engine)

- 日期: 2026-05-20
- 状态: 已确认,待转 implementation plan
- 作者: naruto + Claude

## 1. 背景与问题

当前 `services/relevance_scorer.py` 用单次 Haiku 调用对快讯做多维度评分
(score / sentiment / direction / confidence / affected_tickers),作为推 Bark 前的门控。
建议引擎 `services/suggestions.py` 另有一套打分逻辑。

两个已知问题:

1. **单模型单次判断没有对抗**——容易 anchoring。典型案例:INTW(2x 杠杆 INTC ETF)
   连续 10 个 batch 被建议引擎判为「结构性问题 / SELL / urgency=high」,而它当时正处于
   +13% 的反弹途中,引擎从未把反弹纳入考量,差点让用户卖在地板。
2. 评分**不带外部实时信息**,只看快讯文本本身。

目标:把评分改造成「看多 agent + 看空 agent 对抗辩论 → 中间判官裁决打分」的结构,
并让辩论可以借助 websearch 获取实时上下文。

## 2. 目标 / 非目标

**目标**
- 新建一个共享的「辩论评分」核心模块,快讯门控与买卖建议引擎都调用它。
- 辩论由看多 Haiku + 看空 Haiku(并行)+ 判官 Sonnet 组成,判官产出最终分。
- 辩论可借 websearch 拉实时上下文。
- 对现有调用方尽量做到 drop-in(judge 输出是现有 scorer 字段的超集)。
- 永不丢信号:任何失败都退化为「今天的 triage 行为」。

**非目标**
- 不做多轮反驳辩论(方案 B)。单轮并行即可;未来可加开关。
- 不做判官编排式 agentic 辩论(方案 C)。
- 不引入 migration 框架,沿用项目「直接加列」的风格。
- 不改 Bark 以外的通知渠道。

## 3. 已锁定的决策

| 项 | 决定 |
|---|---|
| 改造目标 | 共享模块,快讯门控 + 买卖建议引擎都接 |
| 辩论触发 | 两阶段门控:triage 每条都跑,只有高 stakes 升级到完整辩论 |
| 升级类快讯推送时机 | 等辩论出完,推一条完整的(放弃这部分的亚秒级时效) |
| 模型档位 | 看多/看空 = Haiku 4.5;判官 = Sonnet 4.6 |
| 架构方案 | 方案 A——单轮并行辩论 |
| websearch | v1 共享预拉一次(多空共用同一份事实底座) |
| 判官 model id | `claude-sonnet-4-6` |
| 僵尸行收尾阈值 / 辩论超时 | 5 分钟 / 90 秒 |
| 实施分期 | Phase 1 快讯侧,Phase 2 建议侧 |

## 4. 架构

### 4.1 模块划分

| 文件 | 角色 |
|---|---|
| `services/relevance_scorer.py` | **阶段 1 triage**,基本不动。`score_relevance()` 继续是单次 Haiku 快评,每条快讯都跑。 |
| `services/debate_scorer.py` | **新建**,阶段 2 辩论内核。对外暴露 `run_debate(content, triage, position_ctx) -> DebateVerdict`。 |
| `services/debate_research.py` | **新建**,websearch 预拉,产出 research brief。实现时若过薄可并入 debate_scorer。 |

设计原则:`debate_scorer` 不认识 macro_pusher / suggestions / jin10_worker,
只吃「内容 + triage 结果 + 持仓上下文」,吐一个 verdict。两个调用方各自接。

### 4.2 两阶段流(快讯侧)

```
快讯 → score_relevance()  [Haiku triage,每条都跑,保亚秒]
       │
       └─ should_escalate(triage, item)?
            否 → 维持现状:score≥阈值 立即推 / 否则只落库
            是 → 入辩论队列(异步,不阻塞采集线程)
                 落库一条 push_status="debating"(dashboard 显示「🧠辩论中…」)
                 后台 consumer 跑 run_debate → 按 verdict 推一条完整 Bark / 或降级落库
```

### 4.3 升级判定 `should_escalate`

默认值,全部进 config 可调:

- `affected_tickers` 非空(点名了用户持仓)→ **升级**
- 或 源 importance ≥ `debate_escalate_min_importance`(默认 5,如 FOMC/CPI,即使无具体 ticker)→ **升级**
- 或 triage `score` 落在临界带 `debate_escalate_score_band`(默认 35–65,triage 自己吃不准)→ **升级**
- 其余(score < 35 噪声、或高分但跟持仓无关)→ 不升级,走原快路

非升级分支行为不变:`score ≥ relevance_threshold` 立即推,否则只落库。

## 5. 辩论内核 `run_debate`

```
① research:1 次带 web_search 工具的 Haiku 调用
   → 针对 affected_tickers / 宏观主题拉近况(近期价格、催化、对立观点)
   → 产出 ~300-500 字 research brief(多空共享同一份事实底座)

② 看多 Haiku  ∥  看空 Haiku   ← 并行,各拿 [快讯 + 持仓上下文 + brief]
   每方输出 JSON:{ stance_score, key_points[], strongest_argument, risks_to_own_view }

③ 判官 Sonnet ← 拿 [快讯 + 持仓上下文 + brief + 多方陈词 + 空方陈词]
   输出最终 DebateVerdict
```

- websearch 用 Anthropic 原生 web search server 工具;`debate_websearch_max_uses` 限制搜索次数;
  具体工具版本号在实现时确认(可借 `claude-api` skill 核对)。
- 喂给辩论的**持仓上下文**比 triage 更丰富:对受影响 ticker 带上该仓的成本/现价/盈亏%/分批成本,
  让多空能像人一样推理(如「05-13 那笔已接近回本」)。

### 5.1 `DebateVerdict` schema

故意做成现有 scorer 字段的**超集**,对 macro_pusher 是 drop-in:

```jsonc
{
  // —— 与现有 scorer 完全一致(调用方无需改解析)——
  "relevance": "direct|indirect|noise",
  "score": 0-100,
  "sentiment": "positive|negative|neutral",
  "direction": "bullish|bearish|neutral",
  "confidence": 0-100,
  "affected_tickers": ["MSFT"],
  "reason": "30字内",
  // —— 辩论新增 ——
  "bull_case": "多方最强论点(判官转述/采纳的)",
  "bear_case": "空方最强论点",
  "judge_reasoning": "判官为何这样裁:谁更有理、哪边证据弱",
  "winning_side": "bull|bear|balanced",
  "model": "debate"
}
```

调用方:macro_pusher 用 score/direction/sentiment 决定推不推、title 怎么写;
新增字段进 Bark body + 落库进 dashboard 详情。

## 6. 异步执行 & 存储

**执行器**:有界 `ThreadPoolExecutor`(`debate_max_workers`,默认 2)。升级时 `submit` 一个辩论任务,
采集线程立刻返回——金十亚秒级那条线不被阻塞。有界 = 突发升级时排队,不会成本/限流爆炸。

**存储时序**(关键:行要在辩论*开始前*落库,兼当去重锁):

```
升级瞬间 → INSERT event_notification,push_status="debating",先填 triage 字段
          (dashboard 显示「🧠辩论中…」;event_hash 已入表 → 下次轮询自动去重)
辩论完成 → UPDATE 同一行:写 debate_json,score/direction/… 覆盖为 verdict,
          push_status = sent / skipped_low_relevance / failed
```

**对账兜底**:macro_flash worker 每轮顺带扫一遍——`debating` 状态超过
`debate_zombie_minutes`(默认 5)的僵尸行,用 triage 分强制收尾(防执行器崩了卡死)。

## 7. 推送行为(快讯侧)

用户选择:等辩论出完,推一次。升级类快讯在升级时**不推**。

- `verdict.score ≥ relevance_threshold` → 推一条完整 Bark
- `< relevance_threshold` → 不推,行更新为 `skipped_low_relevance`

Bark body 布局:

```
判官:看跌 · 综合72 · 可信度65%
多: <bull 一句>
空: <bear 一句>
判官: <judge_reasoning 一句>
<原文摘要>
```

level:`importance ≥ 5` → `timeSensitive`,否则 `active`。

## 8. 买卖建议侧接入(Phase 2)

建议引擎是批量、不急 → 跳过 triage,候选标的直接跑完整辩论:

- 每个 batch 内,对**每个唯一候选 symbol** 跑一次 `run_debate`(同 batch 内按 symbol 缓存);
  喂给辩论的 content = 合成 prompt「此刻该看多还是看空 {symbol}」+ 该标的持仓明细。
- verdict 回填三处:
  1. `direction`/`winning_side` 与建议动作矛盾时 → 抑制或翻转该建议;
  2. **`urgency` 由判官给,不再用静态规则**——这正好修 INTW 那个 bug
     (看多 agent 会把反弹摆上桌 → 判官自然降一档紧迫度);
  3. `thesis` 纳入多/空/判官三段。
- `suggestion` 表加 `debate_json TEXT` 存辩论结果。

## 9. 错误兜底(fail-open,永不丢信号)

| 失败点 | 行为 |
|---|---|
| triage 挂 | 现有 fail-open(score=100,走快路推,不升级) |
| websearch / research 挂或超时 | brief 置空,辩论照跑,agent 被告知「无外部数据」 |
| 看多 **或** 看空 挂 | 判官只拿存活一方裁(prompt 注明对方缺席) |
| 多空**都**挂 / 判官挂 / 整体超时(90s) | 回退 triage verdict,推送带 `[辩论降级]` 标记 |
| 执行器崩溃 | 僵尸行对账(§6)用 triage 分收尾 |

**不变式**:升级最坏只退化成「今天的 triage 行为 + 晚一分钟」,绝不丢信号。

## 10. Schema 改动

沿用「直接加列」风格,不引 migration 框架:

- `event_notification` 加 `debate_json TEXT`(存 `{research_brief, bull, bear, judge_reasoning, winning_side}`)
- `event_notification.push_status` 多一个取值 `debating`
- `suggestion` 加 `debate_json TEXT`

## 11. 测试策略(按项目 TDD 规范)

- **单元**:`should_escalate` 表驱动;verdict JSON 解析/clamp/规范化;每条 fail-open 分支;
  推送 body 格式化。
- **集成**(mock Anthropic client):喂预设多/空/判官响应 → 断言 verdict 合规、
  判官 prompt 含双方陈词。
- **集成 故障注入**:research / bull / bear / judge / 超时 各自失败 → 断言回退 triage、
  信号不丢、行被收尾;去重(升级建 `debating` 行 → 二次轮询被 dedup)。
- **评测脚本**(`backend/scripts/`,非 CI):回放真实快讯(用 5/18–5/20 那批 INTW 新闻)
  跑辩论,人工对眼 verdict。

## 12. Config 新增

```python
debate_enabled: bool = True
debate_bull_model: str = "claude-haiku-4-5-20251001"
debate_bear_model: str = "claude-haiku-4-5-20251001"
debate_judge_model: str = "claude-sonnet-4-6"
debate_escalate_score_band: tuple[int, int] = (35, 65)
debate_escalate_min_importance: int = 5
debate_timeout_seconds: int = 90
debate_zombie_minutes: int = 5
debate_max_workers: int = 2
debate_websearch_enabled: bool = True
debate_websearch_max_uses: int = 3
debate_daily_cap: int = 0    # 0=不限;>0 时超额当天降级走 triage(便宜保险)
```

## 13. 实施分期

- **Phase 1**——内核 + 快讯侧:`debate_scorer` / `debate_research` 模块、schema、执行器、
  config、macro_pusher 与 jin10_browser_worker 接入、僵尸行对账、Phase 1 测试。
- **Phase 2**——建议侧:`suggestions.py` 接入辩论,urgency 改判官给,`suggestion.debate_json`。

每个 Phase 各自走 plan → 实现 → 验证。

## 14. 成本估算

按「每天约 30 条升级快讯 + 约 30 次建议辩论 ≈ 60 次/天」,
Haiku×2 + Sonnet 判官 + websearch,粗估 **~$2–5/天**(websearch 调用量是主要变量)。
`debate_daily_cap` 可作硬上限,默认关闭。
