# 每小时仓位体检 Worker 设计

日期:2026-06-10
状态:已批准,待实现

## 目标

每个整点(24×7)自动跑一次:仓位分析 + 深度市场调研 + 针对我的持仓和操作的指导,将报告落库,并 Bark 推一条摘要。

## 已锁定决策

| 决策点 | 选择 |
| --- | --- |
| 运行时段 | 字面 24×7,每整点一次 |
| Bark 推送 | 每小时都推一条摘要(`active` 级) |
| 调研深度 | 重仓聚焦(只对前 N 大持仓做新闻 + web_search 深调) |
| 定位 | 全新独立 worker + 独立表,不动 suggestions/debate |
| 失败兜底 | fail-soft 逐步降级,绝不整轮崩;AI 整体失败时推「本轮降级」而非静默 |
| 读取接口 | 加 `GET /api/position-analysis/latest`,并复用 `trigger_job_now` 支持「立即跑一次」 |

## 数据流(每整点)

```
load 持仓(list_positions) + 账户快照(get_latest_account)
  → 选重仓:占比 ≥ min_position_pct 的前 top_n 只
  → 逐只拉新闻(news_sources.fetch_news_for_symbol,四级源 fallback)
  → web_search 深度调研(debate_research.gather_research)+ 大盘背景(briefing.fetch_market_context)
  → 喂 Anthropic 原生通道,出结构化指导 JSON
  → 落库 position_analysis_report 表
  → send_bark 推一条摘要
```

## 组件

### 1. 数据表 `position_analysis_report`
文件:`backend/app/models/position_analysis_report.py`

字段:
- `id` (PK)
- `generated_at` (datetime, UTC)
- `account_json` (text/JSON) — 净资产、总市值、现金、日盈亏快照
- `positions_json` (text/JSON) — 本轮被分析的重仓列表
- `research_brief` (text) — web_search 调研简报文本(可空,降级时为空)
- `analysis_json` (text/JSON) — AI 结构化输出
- `summary` (text) — 推送的那句中文摘要
- `push_status` (str) — sent / failed / skipped
- `push_detail` (text) — Bark 返回详情

建表走 `db.py` 的 `Base.metadata.create_all()`;如需新字段用 `_apply_lightweight_migrations()` 模式。

### 2. AI 输出结构(structured JSON)
- `overall_stance` — 整体仓位判断(攻 / 守 / 持)+ 一句理由
- `per_position[]` — 每只重仓:`{symbol, read(解读), guidance(操作指导), signal(信号强度: 强/中/弱)}`
- `alerts[]` — 需要特别注意的点(字符串数组,按重要度排序)
- `summary` — 一句话中文摘要(即 Bark 正文首行)

### 3. AI System Prompt(内置交易风格记忆)
新建专用 system prompt(参考 suggestions.py 的框架但独立),硬编码以下偏好:
- 损失厌恶 + 易补仓 → 点名补仓冲动,但不无脑劝阻
- mega-cap 长仓用前瞻 + 按方向加权,**不要太保守 / 滞后**,别反射性劝降风险
- 偏好期权 income 策略 → 指导带 covered call / cash-secured put 视角;护栏:covered call 必须 ≥100 股正股
- 两可决策给独立判断;纯防御直接给执行动作

### 4. Service
文件:`backend/app/services/position_analysis.py`
- `generate_hourly_analysis(db: Session) -> dict` — 编排整条数据流,返回报告 dict
- 内部分步函数:选重仓、拉新闻、调研、调 AI、解析、落库、推送
- 每步独立 try/except 实现 fail-soft 降级

### 5. Worker
文件:`backend/app/workers/hourly_position_analysis_worker.py`
- `JOB_ID = "hourly-position-analysis"`
- `CronTrigger(minute=0)` 每整点;`max_instances=1, coalesce=True, misfire_grace_time=300`
- `run_in_threadpool(generate_hourly_analysis, db)`
- `record_duration(JOB_ID, ...)`
- 受 `config.hourly_analysis_enabled` 开关控制(关闭时 register 不挂 job)
- `register(sched)` 在 `scheduler.py` 的 `start_scheduler()` 中调用

### 6. 配置(`backend/app/config.py` 新增)
- `hourly_analysis_enabled: bool = True`
- `hourly_analysis_top_n: int = 5`
- `hourly_analysis_min_position_pct: float = 5.0`
- `hourly_analysis_news_per_stock: int = 4`
- `hourly_analysis_model: str`(默认跟 `anthropic_model`)
- `hourly_analysis_websearch_enabled: bool = True`

同步更新 `.env.example`。

### 7. Bark 推送
- `send_bark(title, body, group="position-analysis", level="active")`
- 标题:`📊 仓位体检 · 净资产{net_assets} 日{±day_pnl}`
- 正文:`summary` + alerts 前两条

### 8. 读取接口
文件:`backend/app/api/`(沿用现有路由风格)
- `GET /api/position-analysis/latest` — 返回最近一条报告
- (可选)`GET /api/position-analysis/history?limit=N`
- 「立即跑一次」复用现有 `trigger_job_now("hourly-position-analysis")`

## 复用模块清单

| 用途 | 模块 |
| --- | --- |
| 持仓 / 账户 | `services/positions.list_positions`、`services/account.get_latest_account` |
| 新闻 | `services/news_sources.fetch_news_for_symbol` |
| web_search 调研 | `services/debate_research.gather_research` |
| 大盘背景 | `services/briefing.fetch_market_context` |
| AI 调用 | `ai/chat` / Anthropic client(参考 suggestions.py) |
| Bark | `services/notify.send_bark` |
| 调度 | `workers/scheduler.register / record_duration / trigger_job_now` |
| DB | `db.SessionLocal`、SQLAlchemy models |

## 测试策略

- service 层单测:mock 持仓/新闻/AI 返回,验证 fail-soft 降级(某步异常不崩、降级时 research_brief 为空且仍落库推送)
- 解析 AI JSON 的健壮性(非法 JSON → 降级摘要)
- 落库后可被读取接口取回
- worker register 在 enabled=False 时不挂 job

## 非目标(YAGNI)

- 不做全持仓深调(只重仓)
- 不做前端可视化页面(只给读取 API)
- 不改动现有 suggestions/debate 逻辑
- 不做按市场时段的智能跳过(用户明确要 24×7)
