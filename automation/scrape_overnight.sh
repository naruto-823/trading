#!/bin/zsh
# 从长桥网页版(已登录态)抓取持仓的夜盘/盘前实时价 → /tmp/lb_overnight.json
# 走 agent-browser --cdp 连 Google Chrome 2 调试实例(独立 profile 持久登录)。
# 拿不到时输出空对象,调用方 fail-soft。
export PATH="$PATH:$(npm config get prefix)/bin:$HOME/.local/bin"
PROFILE="$HOME/.lb-trade-profile"
CHROME="/Applications/Google Chrome 2.app/Contents/MacOS/Google Chrome"
OUT="/tmp/lb_overnight.json"
TXT="/tmp/lb_innertext.txt"

# 1) 调试 Chrome 没活着就拉起(登录态在 profile 里,不用重登)
if ! curl -s -o /dev/null --max-time 3 http://127.0.0.1:9222/json/version 2>/dev/null; then
  rm -f "$PROFILE/SingletonLock" 2>/dev/null
  nohup "$CHROME" --remote-debugging-port=9222 --user-data-dir="$PROFILE" \
    --no-first-run --no-default-browser-check "https://trade.longbridge.com" \
    >/tmp/chrome2_debug.log 2>&1 &
  for i in {1..40}; do
    curl -s -o /dev/null --max-time 2 http://127.0.0.1:9222/json/version 2>/dev/null && break
    sleep 1
  done
  sleep 6
fi

# 2) 确保停在交易页
url=$(agent-browser --cdp 9222 get url 2>/dev/null)
case "$url" in
  *trade.longbridge.com*) ;;
  *) agent-browser --cdp 9222 open https://trade.longbridge.com >/dev/null 2>&1; sleep 6 ;;
esac

# 3) 抓页面文本
agent-browser --cdp 9222 eval "document.body.innerText" > "$TXT" 2>/dev/null

# 4) 解析持仓表 → JSON {code: {last, day_pct}}
python3 - "$TXT" "$OUT" << 'PY'
import sys, json, re
raw = open(sys.argv[1]).read()
# eval 输出可能带引号包裹的 JSON 字符串
try:
    if raw.strip().startswith('"'):
        raw = json.loads(raw)
except Exception:
    pass
text = raw.replace("\\n", "\n")
out = {}
# 持仓行以 "平仓\n" 分隔;每段: code, name, 市值, 数量, 最新价, 成本, 可用, 浮盈亏, 浮盈亏率, 当日盈亏, 当日率, 占比
chunks = text.split("平仓\n")
num = re.compile(r'^-?[\d,]+\.?\d*$')
for ch in chunks[1:]:
    lines = [l.strip() for l in ch.split("\n")]
    if len(lines) < 6:
        continue
    code = lines[0]
    # code 形如 GOOG / META / "PDD 270115 120 Call"
    if not code or len(code) > 30 or "\t" in code:
        continue
    last = lines[4] if len(lines) > 4 else ""
    day_pct = lines[10] if len(lines) > 10 else ""
    if num.match(last.replace(",", "")):
        out[code] = {"last": last, "day_pct": day_pct}
print(json.dumps(out, ensure_ascii=False))
json.dump({"_scraped_at": None, "quotes": out}, open(sys.argv[2], "w"), ensure_ascii=False)
PY
echo "[scrape_overnight] wrote $OUT"
