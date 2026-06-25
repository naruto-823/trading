# Discord 个股新闻数据源接入 — 设计

- 日期:2026-06-25
- 状态:设计已确认,待写实现计划
- 关联:复用 [[2026-05-20-debate-scorer-design]] 的辩论升级、Jin10 实时管道(`jin10_browser_worker.py`)

## 1. 背景与目标

把第三方付费 Discord 服务器 **BubbleSeek** 里某个 bot(「黄金蛙 APP」)发布的「24 小时个股新闻总结」频道,作为一个**增量新闻数据源**接入 trading 后端,与现有 Jin10 实时管道**并列**:抓到的个股新闻经评分、去重、看多看空辩论门控后,高价值的推 Bark。

- 第一期只接一个频道:`guild=1394858797025198150 / channel=1431922532872880278`。
- 架构做成**频道可配置**,后续加频道只改配置 + 视情况加解析器。

## 2. 接入方式决策:浏览器登录态(非官方 API)

调研结论(2026-06-25):对"读第三方付费群、用户为普通成员"这个场景,**没有干净的官方 API 路子**。

| 方式 | 结论 | 原因 |
|---|---|---|
| 官方 Bot + Token | ❌ 不可行 | Bot 读消息需被**服务器管理员**邀请进服务器 + 开 Message Content 特权 intent。用户非管理员,且付费群群主不会让外部 bot 进来。 |
| User Token / Self-bot | ⚠️ 违规高危 | 违反 Discord 社区准则(账号须对应真人),判定 self-bot 会封号,连带丢失付费群访问权。 |
| **浏览器登录态读 DOM** | ✅ 采用 | 自动化用户自己的真人登录态、读本来有权看的页面;不调私有 API、不发消息、不冒充 bot。灰色但风险最低。 |

**遗留合规可能(用户侧跟进)**:若「黄金蛙」bot 本身有官方订阅/API/RSS,直接订源更合规。需用户向群主/同事确认;在此之前走浏览器登录态方案。

**风险认账**:浏览器登录态自动化仍属 Discord ToS 灰色地带,封号风险虽低但非零。用户已知悉并接受。缓解:真实登录态 + 低频 poll(10s) + 真实 UA + 只读不发。

## 3. 架构总览

```
专用调试 Chrome(.discord-profile,用户首次扫码登录)
  └─ Playwright 连接 → 打开 discord.com/channels/<guild>/<channel>
       └─ 注入 JS hook:poll 消息容器底部 + message-id diff 抓新消息
            └─ 每条 bot 消息(「24h个股新闻总结」,含 ~10 条)
                 └─ discord_parser:拆成 N 条 {ticker, text}
                      └─ news_pipeline(复用 Jin10 逻辑):
                           matches_keywords → event_notification 去重
                           → score_relevance 评分
                           → should_escalate? 升级辩论(debate_scorer)
                           → 高分推 Bark / 低分落库跳过
```

## 4. 组件分解

| 组件 | 新建/复用 | 职责 | 依赖 |
|---|---|---|---|
| `workers/discord_news_worker.py` | 新建 | 连持久 profile、注入 hook、收新消息、自动重连。对标 `jin10_browser_worker.py`。 | Playwright、`news_pipeline`、`discord_parser` |
| `services/discord_parser.py` | 新建 | 把一条「24h总结」拆成 `list[{ticker, text}]`;格式异常时 fail-soft 退化为整条。 | 无(纯函数,易测) |
| `services/news_pipeline.py` | **重构抽取** | 把 Jin10 现有 `_process_flash_sync`(评分/去重/门控/推送)抽成共享函数,Jin10 与 Discord 同调,**不复制代码**。 | `score_relevance`、`event_notification`、`debate_queue`、`notify` |
| `automation/discord_login.sh` | 新建 | 对标 `scrape_overnight.sh`,拉起 `.discord-profile` 调试实例供首次登录。 | — |

**边界清晰性**:`discord_parser` 纯函数无 IO(可单测);`news_pipeline` 只认 `{ticker, text, source, importance}` 抽象,不关心消息来自 Jin10 还是 Discord;`discord_news_worker` 只管浏览器与消息抓取,处理逻辑全委托给 pipeline。

## 5. 数据流与关键难点

### 难点 A:重叠去重(成败关键)

bot **每整点发「24h 滚动总结」**,相邻整点消息大量重复同一批新闻。

- **去重粒度 = 单条新闻内容,非 Discord 消息**。拆条后每条算 `sha256("discord-news|{ticker}|{正文前120字}")` 作 `event_hash`,落 `event_notification`。
- 与 Jin10 **共享同一张去重表** → 金十先报、Discord 再现也不双推。
- 去重做对 = 干净增量源;做错 = 每小时刷屏。

### 难点 B:拆条解析

bot 消息格式固定(编号列表 `1. $TICKER xxx`)。`discord_parser` 正则拆条:
- 提取每条 `$TICKER` 直接作 `affected_tickers`(比 `score_relevance` 反推更准)。
- 容错:无编号/无 `$` 时 fail-soft,整条退化为一条处理,不崩、不丢。

### 难点 C:Discord DOM 抓取

Discord 重 SPA + 虚拟滚动(只渲染可见消息)。
- hook 只 poll **底部最新消息**(`li[id^="chat-messages-"]` 末几条),按 message-id diff。
- poll 间隔 **10s**(bot 整点才发,无需 2s)。
- 实现时需到真实登录态页面核对选择器。

## 6. 错误处理(全 fail-soft,worker 不崩)

- **登录态过期**(被踢回登录页):标记 `last_error` + **推 Bark 提醒重新扫码**(对标长桥 cookie 过期),不崩不刷屏。
- **DOM 选择器失效**:记录错误、保持重试,不误推。
- **拆条失败**:退化整条处理,不丢消息。
- **浏览器崩溃**:自动重连 + 6h 定时刷页(复用 Jin10 循环)。

## 7. 测试

- `discord_parser` 单测:喂两条真实「24h总结」原文,断言拆出条数与 ticker 正确($QCOM/$MU/$BABA…)。
- **去重单测**:同一条新闻二次进入必被 dedup(防整点刷屏的核心保障)。
- **回归保护**:`news_pipeline` 抽取后跑 Jin10 现有测试确保仍绿。

## 8. 开关与配置

- `settings.discord_news_realtime` **默认 False**(对标 `jin10_browser_realtime`,显式开启)。
- `DISCORD_CHANNELS = [{guild, channel, parser}]` 配置化。
- `status()` 监控:`connected / received / deduped / fired / last_error`,对标 Jin10。
- profile 路径:`~/.discord-profile`(与 `.lb-trade-profile` 隔离)。

## 9. 第一期范围(YAGNI)

- ✅ 一个频道、一个解析器、全套评分去重推送、默认关、可配置。
- ❌ 不做:多频道多解析器、期权大单/KOL 等异格式频道、官方 API 集成、历史回溯抓取。
