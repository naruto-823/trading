#!/bin/zsh
# 每小时仓位体检 —— 用 Claude Code (headless) 跑深度研究 + 落库 + Bark 推送。
# 由 launchd 每整点触发。本脚本部署在 ~/trading-hourly(非 TCC 受保护目录),
# 数据全走 localhost:8000 后端,报告写本目录 reports/,不碰 ~/Desktop。
# 日志见 /tmp/trading_hourly_analysis.log。

set -u
LIVE="$HOME/trading-hourly"
PROMPT="$LIVE/hourly_analysis_prompt.md"
LOG="/tmp/trading_hourly_analysis.log"
LOCK="/tmp/trading_hourly_analysis.lock"
CLAUDE="/Users/naruo/.local/bin/claude"

ts() { date "+%Y-%m-%d %H:%M:%S"; }

if ! mkdir "$LOCK" 2>/dev/null; then
  echo "[$(ts)] 上一轮仍在运行,跳过本轮" >> "$LOG"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

if ! curl -s -o /dev/null --max-time 6 http://127.0.0.1:8000/api/health; then
  echo "[$(ts)] 后端未就绪(8000 不通),跳过" >> "$LOG"
  exit 0
fi

echo "[$(ts)] ===== 开始本轮体检 =====" >> "$LOG"
cd "$LIVE" || exit 1
rm -f /tmp/pa_ingest.json   # 清掉上一轮残留,避免 claude 误判为"续跑"

# 先从长桥网页(登录态)抓夜盘/盘前实时价 → /tmp/lb_overnight.json(fail-soft,失败不阻断)
rm -f /tmp/lb_overnight.json 2>/dev/null
"$LIVE/scrape_overnight.sh" >> "$LOG" 2>&1 || echo "[$(ts)] 夜盘抓取失败,本轮用后端常规价" >> "$LOG"

"$CLAUDE" -p "$(cat "$PROMPT")" \
  --allowedTools 'WebSearch' 'WebFetch' 'Bash(curl:*)' 'Bash(date:*)' 'Write' 'Read' \
  >> "$LOG" 2>&1

echo "[$(ts)] ===== 本轮结束 (exit $?) =====" >> "$LOG"
