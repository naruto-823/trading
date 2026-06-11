# 每小时仓位体检 —— Claude Code 自动化

用 **Claude Code (headless)** 而非中转 API 跑定时深度分析:真 WebSearch 联网研究 + 强模型 + 长报告。
取代了后端 APScheduler 里的中转 worker(那条中转代理不支持 web_search、且输出被紧 JSON 框住,太简略)。

## 组成
- `hourly_analysis_prompt.md` —— 分析指令(内置交易风格 + 输出要求)。改分析逻辑改这里。
- `run_hourly_analysis.sh` —— 壳脚本:健康检查 + 防重叠锁 + `claude -p` 白名单跑分析。
- `com.naruo.trading-hourly-analysis.plist` —— launchd 定义,每整点 :00 触发(本机,登录态运行)。

## 数据流(每整点)
```
launchd → run_hourly_analysis.sh → claude -p (--allowedTools WebSearch,WebFetch,Bash(curl/date),Write,Read)
  claude 自主:
   curl localhost:8000/api/positions + /api/account   # 拿真实全持仓
   → WebSearch 逐个标的深研 + 大盘宏观
   → 写完整 markdown 到 reports/<时间戳>.md
   → POST /api/position-analysis/ingest               # 落库 + Bark 精炼推送
```
报告全文经后端落库,`GET /api/position-analysis/latest`、`/history` 可查;同时存一份在 `reports/`。

## 运维
- **日志**:`/tmp/trading_hourly_analysis.log`(脚本+claude 输出)、`/tmp/trading_hourly_analysis.launchd.{log,err}`(launchd)
- **手动跑一次**:`./automation/run_hourly_analysis.sh`
- **临时停**:`launchctl unload ~/Library/LaunchAgents/com.naruo.trading-hourly-analysis.plist`
- **重新装**:`launchctl load -w ~/Library/LaunchAgents/com.naruo.trading-hourly-analysis.plist`
- **看是否注册**:`launchctl list | grep trading-hourly`
- **改频率**:编辑 plist 的 `StartCalendarInterval`(如要市场时段才跑,改成多个 `<dict>` 数组),改完 unload+load。

## 前提
- 后端在本机 8000 端口跑着(claude 要 curl 拿持仓);后端 `.env` 里 `HOURLY_ANALYSIS_ENABLED=false`(关掉中转 worker 免双推)。
- `claude` CLI 已登录(`~/.claude/.credentials.json`),走你的 Claude Code 订阅额度。
- 权限:脚本用 `--allowedTools` 精确白名单(**不**用 `--dangerously-skip-permissions`),未列出的工具一律拒绝。
