#!/bin/zsh
# 每小时仓位体检 —— 用 Claude Code (headless) 跑深度研究 + 落库 + Bark 推送。
# 由 launchd 每整点触发。日志见 /tmp/trading_hourly_analysis.log。

set -u
PROJ="/Users/naruo/Desktop/work/ai/trading"
PROMPT="$PROJ/automation/hourly_analysis_prompt.md"
LOG="/tmp/trading_hourly_analysis.log"
LOCK="/tmp/trading_hourly_analysis.lock"
CLAUDE="/Users/naruo/.local/bin/claude"

ts() { date "+%Y-%m-%d %H:%M:%S"; }

# 防重叠:上一轮没跑完就跳过这轮(mkdir 原子锁)
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "[$(ts)] 上一轮仍在运行,跳过本轮" >> "$LOG"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# 后端没起来就别跑(claude 拿不到持仓)
if ! curl -s -o /dev/null --max-time 6 http://127.0.0.1:8000/api/health; then
  echo "[$(ts)] 后端未就绪(8000 不通),跳过" >> "$LOG"
  exit 0
fi

echo "[$(ts)] ===== 开始本轮体检 =====" >> "$LOG"
cd "$PROJ" || exit 1

# headless 跑分析。用精确工具白名单(不放开全部权限):
# 只允许 联网研究 + 读后端(curl)+ 取时间戳(date)+ 写报告文件。未列出的工具一律拒绝,
# claude 会自适应、不会卡住等输入。
"$CLAUDE" -p "$(cat "$PROMPT")" \
  --allowedTools 'WebSearch' 'WebFetch' 'Bash(curl:*)' 'Bash(date:*)' 'Write' 'Read' \
  >> "$LOG" 2>&1

echo "[$(ts)] ===== 本轮结束 (exit $?) =====" >> "$LOG"
