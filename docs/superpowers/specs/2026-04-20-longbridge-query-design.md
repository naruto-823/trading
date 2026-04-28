# AI Trading · 首期设计：长桥账户查询 + 对话式 AI

- 日期：2026-04-20
- 状态：Draft（待实施）
- 目标读者：后续会实现本期功能的工程师/AI agent

## 1. 背景与目标

初始化 `ai-trading` 项目。首期目标：让用户能在本地 Web 界面查看自己的长桥账户数据（账户概览、持仓、历史成交、历史盈亏、实时报价），并通过对话式 AI 助手用自然语言提问这些数据。首期不做下单、不做 A 股、不做 K 线/策略回测。

项目名含"AI"，因此首期即接入 Claude 作为对话助手，把"查询长桥数据"作为工具（tool use）暴露给模型。

## 2. 范围

### In-scope（首期）
- 账户概览（净资产 / 现金 / 市值 / 今日盈亏 / 总盈亏）
- 当前持仓列表（含成本、市价、浮动盈亏）
- 历史成交 / 历史订单
- 历史盈亏（直接使用长桥官方返回值，不自建 FIFO）
- 实时报价（按需查询，不落库）
- 市场：港股、美股
- 对话式 AI 助手（Claude，tool use）
- 手动触发同步（点按钮 / CLI 命令）

### Out-of-scope（明确不做）
- 下单、改单、撤单
- A 股、新加坡等其他市场
- K 线 / 分时图 / WebSocket 行情推送
- 自建 FIFO 盈亏、策略回测、量化信号
- 多账户
- 后台定时同步（首期只做手动触发，后续再加）

## 3. 架构总览

**方案 A：单体 FastAPI + 独立前端（同仓库）**

```
Browser (React + Vite)  ──►  FastAPI (:8000)  ──►  长桥 OpenAPI
                                 │
                                 ├──►  SQLite (data/trading.db)
                                 │
                                 └──►  Anthropic API (Claude)
```

- 后端、前端各自独立进程，开发期并行启动
- 前端通过 Vite dev proxy 把 `/api` 转发到后端
- AI 走后端 `/api/chat` 流式 SSE，tool use 在服务端闭环完成

## 4. 目录结构

```
trading/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI 入口、路由注册、CORS
│   │   ├── config.py          # .env 加载(pydantic-settings)
│   │   ├── db.py              # SQLAlchemy engine/session、SQLite 初始化
│   │   ├── models/            # ORM: AccountSnapshot, Position, Execution, Order, SyncLog
│   │   ├── schemas/           # Pydantic 响应模型
│   │   ├── longbridge/
│   │   │   ├── client.py      # QuoteContext/TradeContext 单例管理
│   │   │   └── sync.py        # 拉账户/持仓/成交/订单 写入 SQLite
│   │   ├── services/          # account, positions, executions, orders, pnl, quote
│   │   ├── api/               # 路由: account, trades, quotes, sync, chat, health
│   │   └── ai/
│   │       ├── tools.py       # Claude tool 定义 + 映射到 services
│   │       └── chat.py        # 对话循环、SSE 流式
│   ├── tests/
│   ├── pyproject.toml         # uv 管理依赖
│   ├── .env.example
│   └── data/trading.db        # 运行时 SQLite(.gitignore)
├── frontend/
│   ├── src/
│   │   ├── pages/             # Dashboard, Executions, PnL, Chat
│   │   ├── components/        # 表格/卡片/图表封装 + shadcn/ui
│   │   ├── api/               # fetch 封装 + openapi-typescript 生成类型
│   │   └── App.tsx            # 路由
│   ├── vite.config.ts         # proxy /api → :8000
│   └── package.json
├── docs/superpowers/specs/    # 设计/计划文档
├── Makefile                   # make dev/test/sync/setup
├── README.md
└── .gitignore
```

## 5. 数据模型（SQLite）

```
account_snapshot
  id PK, synced_at, currency, total_cash, net_assets, market_value,
  total_pnl, day_pnl, raw_json

position                  -- 每次同步先 TRUNCATE 再全量写入,UNIQUE(symbol, market)
  id PK, synced_at, symbol, market, name, quantity, available_qty,
  cost_price, current_price, market_value, unrealized_pnl,
  unrealized_pnl_ratio, currency, raw_json

execution                 -- 只追加,execution_id 去重
  execution_id PK, order_id, symbol, market, side,
  price, quantity, trade_done_at, currency,
  commission, platform_fee, raw_json

order                     -- 订单粒度
  order_id PK, symbol, market, side, order_type, status,
  submitted_qty, filled_qty, avg_price, submitted_at, updated_at, raw_json

sync_log
  id PK, kind, started_at, finished_at, status, error, rows_written
```

**要点：**
- `raw_json` 保留 SDK 原始响应，便于后续补指标
- 持仓用快照覆盖策略，日后需要历史持仓曲线再加 `position_history`
- 成交增量同步以 `max(trade_done_at)` 为起点
- 官方返回的已实现/未实现 PnL 直接落库

## 6. 后端 API

| Method | Path | 说明 |
|---|---|---|
| GET  | `/api/health`                        | 健康检查 |
| POST | `/api/sync/all`                      | 同步账户+持仓+订单+成交 |
| POST | `/api/sync/{kind}`                   | 单项同步：`account` / `positions` / `orders` / `executions` |
| GET  | `/api/account`                       | 最新账户快照 |
| GET  | `/api/positions`                     | 当前持仓（`?market=HK,US`） |
| GET  | `/api/executions`                    | 历史成交（`?symbol=&from=&to=&page=&size=`） |
| GET  | `/api/orders`                        | 历史订单（同上过滤） |
| GET  | `/api/pnl/summary`                   | 按标的/市场聚合盈亏 |
| GET  | `/api/quote?symbols=AAPL.US,700.HK`  | 实时报价（透传 SDK，不落库） |
| POST | `/api/chat` (SSE)                    | 对话 AI，body: `{messages: [...]}`，流式 |
| GET  | `/api/sync/logs?limit=20`            | 最近同步记录 |

**规范：**
- 响应统一 `{ data, error }` 包裹
- SDK 调用错误封装为 `sync_log` + 结构化 `error { code, message, retryable }`
- `/api/chat` 使用 Anthropic `messages.stream`，工具调用服务端闭环

## 7. AI 对话（Claude tool use）

### 模型与 SDK
- 默认 `claude-opus-4-7`，env `AI_MODEL` 可切 `claude-sonnet-4-6`
- `anthropic` Python SDK，`messages.stream`
- system + tools 定义加 `cache_control` 启用 prompt caching

### Tools

| 工具 | 入参 | 行为 |
|---|---|---|
| `get_account`       | —                              | 读 `account_snapshot` 最新 |
| `list_positions`    | `market?`                      | 读 `position` 最新快照 |
| `list_executions`   | `symbol?, from?, to?, limit?`  | 读 `execution` |
| `list_orders`       | `symbol?, from?, to?, status?` | 读 `order` |
| `get_pnl_summary`   | `group_by? (symbol/market)`    | 聚合盈亏 |
| `get_quote`         | `symbols[]`                    | 实时调 SDK |
| `sync_now`          | `kind? (all/account/...)`      | 触发同步 |

### 对话循环

1. 前端 POST `/api/chat` 携带 `messages`
2. 后端拼 system prompt（身份、注入当前日期、"用中文简洁回答、优先使用工具"）
3. `stream` → `tool_use` → 本地执行 → 回填 `tool_result` → 再开一轮，直到 `end_turn`
4. 前端按 SSE 事件渲染：`text` 追加气泡，`tool_use` 显示状态条（"正在查询持仓…"）

### 安全与限制
- 工具异常以 `tool_result.is_error=true` 回传给模型
- 前端不能伪造 `tool_result`，全由服务端生成
- 单轮最多 8 次 tool 调用（防死循环）
- 首期只读 + 触发同步，无下单工具

## 8. 前端

- 技术栈：React + Vite + TypeScript + TanStack Query + Tailwind + shadcn/ui + Recharts
- API 类型通过 `openapi-typescript` 从 FastAPI `/openapi.json` 生成
- 页面：
  - `Dashboard`：账户卡片 + 持仓表 + 同步按钮 + 最近同步时间
  - `Executions`：可过滤成交表（标的/日期范围/方向）
  - `PnL`：按标的聚合表 + 条形图
  - `Chat`：全高对话界面，流式气泡 + tool-use 状态条 + 快捷 prompt 侧栏

## 9. 错误处理

- 后端：SDK 错误结构化返回，不 500；`sync` 错误写入 `sync_log` 并返回详细字段
- 前端：全局 toast + `ErrorBoundary`；同步失败在同步按钮/最近同步时间处高亮
- `.env` 缺失：启动即失败并输出指引，指向 `.env.example`

## 10. 测试

- 后端：
  - `longport` SDK 用 mock；`services/` 纯逻辑单测；`/api/*` 用 FastAPI `TestClient` 做端到端
  - AI tool 调度用 fake Anthropic client，断言工具被正确调用 + 结果回传
- 前端：
  - 关键组件测试（同步按钮、过滤器、聊天流式）
  - API 层用 MSW 打桩
- `make test` 串起两边

## 11. DX 与交付

- Python：`uv` 管依赖，`ruff` + `mypy`
- Node：`pnpm`，`eslint` + `prettier`
- `Makefile`：
  - `make setup`：安装依赖、复制 `.env.example`
  - `make dev`：并行起后端和前端
  - `make sync`：命令行同步一次（cron 兜底用）
  - `make test` / `make lint`
- `README.md`：长桥 App Key 申请指引 + 启动 3 步 + 截图位
- `.env.example` 必含：`LONGPORT_APP_KEY`、`LONGPORT_APP_SECRET`、`LONGPORT_ACCESS_TOKEN`、`ANTHROPIC_API_KEY`、`AI_MODEL`

## 12. 风险与未决

- **长桥 API 速率限制**：首期手动同步影响有限，后续做自动同步需加退避
- **港美股报价权限**：`get_quote` 依赖账户的 LV1 权限，无权限时给清晰提示
- **盈亏指标口径**：首期用官方值，后续要做"按标的实现盈亏/胜率/持有时长"时再单开一期

## 13. 后续迭代候选

- 后台定时同步（scheduler + 指数退避）
- 自建 FIFO 盈亏 + 胜率/持有时长
- K 线 / 分时 / WebSocket 实时推送
- AI：按钮触发的"这笔交易分析"、策略建议
- 下单工具（独立授权与二次确认流程）
- A 股接入
