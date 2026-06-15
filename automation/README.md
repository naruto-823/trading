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

## ⚠️ macOS TCC 坑(必读)
launchd 后台任务**无法读取 `~/Desktop`/`~/Documents`/`~/Downloads`**(系统隐私保护),
否则报 `/bin/zsh: can't open input file ...`、退出码 127、静默不跑。
因此**实际部署的脚本+prompt+报告输出放在 `~/trading-hourly/`(非受保护目录)**,
本仓库 `automation/` 只是版本管理的源副本。修改后需同步:
```
cp automation/run_hourly_analysis.sh automation/hourly_analysis_prompt.md ~/trading-hourly/
```
plist 的 ProgramArguments 指向 `~/trading-hourly/run_hourly_analysis.sh`。
数据全走 localhost:8000(网络调用不受 TCC 限制),所以后端可留在 Desktop 项目里。

## ⚠️ 依赖:后端必须常驻
脚本每轮先 `curl localhost:8000/api/health`,**后端不在就跳过这一轮**。
后端进程需保持运行(目前未做成开机自启;后端也在 Desktop 项目内,做 launchd 服务同样受 TCC 限制,
需要移出 Desktop 或给 Python 授 Full Disk Access)。

## 夜盘/盘前实时价(0 成本,走网页登录态)
长桥 OpenAPI 夜盘行情卡要 500/月,不买。改从**长桥网页版登录态**白嫖:
- `scrape_overnight.sh`:用 `agent-browser --cdp 9222` 连一个**带调试端口的 Google Chrome 2 实例**
  (独立 profile `~/.lb-trade-profile`,一次性登录后持久),`eval document.body.innerText`
  抓持仓表 → 解析成 `/tmp/lb_overnight.json`(`{quotes:{META:{last,day_pct},...}}`)。
- wrapper 每轮先跑它(fail-soft);prompt 让 claude 优先用 `/tmp/lb_overnight.json` 的 `last` 当现价。
- Chrome 2 调试实例挂了脚本会自动用持久 profile 拉起(登录态在,不用重登)。
- **唯一需人工**:网页登录 cookie 自然过期后(数天~数周),抓到的是登录页 → 无 quotes →
  自动退回后端收盘价;届时重新在那个 Chrome 2 窗口登录一次即可。
- 启动调试 Chrome 2(脚本会自动做,手动用):
  `"/Applications/Google Chrome 2.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --user-data-dir=~/.lb-trade-profile https://trade.longbridge.com`
