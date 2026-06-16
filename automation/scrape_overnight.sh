#!/bin/zsh
# 从长桥网页版(已登录态)抓取持仓的夜盘/盘前实时价 → /tmp/lb_overnight.json
# 走 agent-browser --cdp 连 Google Chrome 2 调试实例(独立 profile 持久登录)。
# 带重试:SPA 持仓表懒加载,偶发抓空 → 滚动/刷新重试最多 3 次。拿不到输出空对象(调用方 fail-soft)。
export PATH="$PATH:$(npm config get prefix)/bin:$HOME/.local/bin"
PROFILE="$HOME/.lb-trade-profile"
CHROME="/Applications/Google Chrome 2.app/Contents/MacOS/Google Chrome"
OUT="/tmp/lb_overnight.json"
TXT="/tmp/lb_innertext.txt"

# 1) 调试 Chrome 没活着就拉起(登录态在 profile 里)
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

parse() {
  python3 - "$TXT" "$OUT" << 'PY'
import sys, json, re
raw = open(sys.argv[1]).read()
try:
    if raw.strip().startswith('"'): raw = json.loads(raw)
except Exception: pass
text = raw.replace("\\n", "\n")
out = {}
num = re.compile(r'^-?[\d,]+\.?\d*$')
for ch in text.split("平仓\n")[1:]:
    lines = [l.strip() for l in ch.split("\n")]
    if len(lines) < 6: continue
    code = lines[0]
    if not code or len(code) > 30: continue
    last = lines[4] if len(lines) > 4 else ""
    day_pct = lines[10] if len(lines) > 10 else ""
    if num.match(last.replace(",", "")):
        out[code] = {"last": last, "day_pct": day_pct}
json.dump({"_scraped_at": None, "quotes": out}, open(sys.argv[2], "w"), ensure_ascii=False)
print(len(out))
PY
}

# 3) 滚动渲染 + 抓取 + 解析,带重试(SPA 懒加载偶发抓空)
ok=0
for attempt in 1 2 3; do
  agent-browser --cdp 9222 scroll up 3000 >/dev/null 2>&1; sleep 1
  agent-browser --cdp 9222 scroll down 1800 >/dev/null 2>&1; sleep 2
  agent-browser --cdp 9222 eval "document.body.innerText" > "$TXT" 2>/dev/null
  cnt=$(parse)
  if [ "${cnt:-0}" -ge 3 ] 2>/dev/null; then
    ok=1; echo "[scrape_overnight] attempt $attempt 拿到 $cnt 只"; break
  fi
  echo "[scrape_overnight] attempt $attempt 抓到 ${cnt:-0} 只,重试..."
  # 第 2 次起重新加载页面(更强的刷新)
  agent-browser --cdp 9222 open https://trade.longbridge.com >/dev/null 2>&1; sleep 6
done

if [ "$ok" != 1 ]; then
  echo '{"_scraped_at":null,"quotes":{}}' > "$OUT"
  echo "[scrape_overnight] 3 次仍空,输出空对象(fail-soft)"
fi
echo "[scrape_overnight] wrote $OUT (ok=$ok)"
