# AI Trading

本地 Web 界面查看长桥账户数据（账户概览、持仓、历史成交、盈亏、实时报价），并通过对话式 AI 助手用自然语言提问。

## 快速开始

### 1. 准备凭证

- **长桥 OpenAPI**：前往 [长桥开放平台](https://open.longportapp.com/docs) 申请 App Key / Secret / Access Token
- **Anthropic API Key**：前往 [Anthropic Console](https://console.anthropic.com/) 获取

### 2. 安装与配置

```bash
make setup
# 编辑 .env 填入你的凭证
```

### 3. 启动

```bash
make dev
```

- 后端：http://localhost:8000
- 前端：http://localhost:5173

### 其他命令

```bash
make sync    # 命令行手动同步一次长桥数据
make test    # 运行测试
make lint    # 代码检查
```

## 技术栈

- **后端**：FastAPI + SQLAlchemy + SQLite + longport SDK + Anthropic SDK
- **前端**：React + Vite + TypeScript + TanStack Query + Tailwind CSS + shadcn/ui + Recharts

## 常驻服务器 Bark 异常提醒

独立的只读任务每60秒读取默认账户，依赖长桥真实账户和已授权行情，不调用AI、不下单。
交易日历和纽约时区用于区分正式盘、盘前、盘后和夜盘；超过180秒的行情不用于触发价格告警。

| 检查 | 初始阈值 |
| --- | --- |
| 券商追保 | margin_call > 0，立即提醒 |
| 账户净值变化 | 相对本轮采集基准−1.5% / −3%，剔除已识别外部出入金 |
| QQQ | 正式收盘参考跌幅−2.5%，或完整5分钟窗口−1% |
| 其他美股 / 港股 | 正式收盘参考跌幅−4% / −6%，或完整15分钟窗口−2.5% |
| 净卖出期权增加 | 提醒核对方向、乘数、到期和覆盖，不推定100股或已发生指派 |
| 数据缺失 | 账户读取失败、行情过期、流水口径不完整分别提醒 |

账户基准在北京时间16:00后首次成功采集重建，并非券商正式日收益。USD/HKD默认7.85，可配置；
未知流水类型会暂停精确回撤归因。盘前/盘后/夜盘以最近正式收盘为参考，不冒用正式盘旧价。
覆盖仅限默认账户的美港股票/ETF及净卖出期权变化，不提供期权实时估值、完整指派确认或宏观事件预测。
新闻仍使用项目已有通道，其可用性取决于已有数据源/AI开关，本任务不自动开启付费AI。

同一异常按交易时段或账户基准日去重，升级严重度可立即再次提醒。价格/回撤恢复至阈值一半以内且
距离上次推送已30分钟后可重新触发。推送失败下次重试，状态保存在SQLite持久卷；
迟到通知以正文采集时间为准。连续异常不会每分钟刷屏，通知也不保证预测后续跌幅。

上线步骤（凭证只保存在环境变量 / GitHub Secrets，不提交仓库）：

1. 在阿里云确认可用实例、当前IP及SSH公钥指纹。旧实例过期/释放时，不能直接信任旧IP的新指纹。
2. 配置现有部署Secrets：`DEPLOY_HOST`、`DEPLOY_USER`、`DEPLOY_SSH_KEY`、`DEPLOY_KNOWN_HOSTS`、`APP_ENV`。
   `APP_ENV`包含长桥凭证、`BARK_DEVICE_KEY`、`BARK_BASE_URL`及`RISK_MONITOR_ENABLED=true`。
   同时更新`PRODUCTION_URL`；每次部署会从`APP_ENV`覆盖服务器配置。
3. 保留公网原有认证；部署后确认`/api/system/risk-monitor`中的`healthy=true`，
   `coverage_complete`及`coverage_issues`说明行情/流水覆盖。该接口不返回账户金额和凭证。
4. 从已有通知测试入口发一条Bark测试并在手机核对，再启用仓库变量`RISK_MONITOR_WATCHDOG_ENABLED=true`。

外部`Portfolio monitor watchdog`工作流计划每5分钟检查一次（GitHub调度可能延迟，不承诺5分钟必达）。
它复用固定指纹的SSH和Bark配置，连续两次失败通知一次、恢复通知一次；状态缓存仅保存失败次数和通知状态。
缓存丢失可能再次提醒；GitHub停止调度或Bark本身故障时不能保证手机告警。
默认关闭，只有验证服务器和Bark后才启用。停止监控时同时关闭上述仓库变量和`RISK_MONITOR_ENABLED`。
