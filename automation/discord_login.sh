#!/bin/zsh
# 拉起一个带远程调试口(9223)的持久 profile Chrome,停在目标 Discord 频道,供你首次扫码登录。
# 登录态存进 ~/.discord-profile;之后 discord_news_worker 用 connect_over_cdp 连它读消息。
# 跟长桥 .lb-trade-profile / 9222 隔离,互不影响。
export PATH="$PATH:$(npm config get prefix)/bin:$HOME/.local/bin"
PROFILE="$HOME/.discord-profile"
CHROME_APP="Google Chrome 2"   # open -na 用 app 名;同 bundle 多实例靠 -n + 独立 user-data-dir
PORT=9223
GUILD="1394858797025198150"
CHANNEL="1431922532872880278"
URL="https://discord.com/channels/${GUILD}/${CHANNEL}"

# 检测 9223 上是否已有可用页面(光有 CDP version 不够——同 app 多实例时会出现
# CDP 活着但 0 标签的情况)。没有就用 open -n 强制起独立实例:Chrome 2 已在跑长桥
# (9222)时,直接 exec 二进制会被已运行实例拦截、新窗口不创建;open -na 才可靠。
pages=$(curl -s --max-time 3 "http://127.0.0.1:${PORT}/json" 2>/dev/null \
  | grep -c '"type": "page"' 2>/dev/null)
if [ "${pages:-0}" -lt 1 ] 2>/dev/null; then
  pkill -f "\.discord-profile" 2>/dev/null; sleep 2
  rm -f "$PROFILE/SingletonLock" 2>/dev/null
  open -na "$CHROME_APP" --args \
    --user-data-dir="$PROFILE" --remote-debugging-port=${PORT} \
    --no-first-run --no-default-browser-check "$URL"
  for i in {1..40}; do
    p=$(curl -s --max-time 2 "http://127.0.0.1:${PORT}/json" 2>/dev/null | grep -c '"type": "page"')
    [ "${p:-0}" -ge 1 ] 2>/dev/null && break
    sleep 1
  done
  sleep 3
fi

echo "[discord_login] Chrome 调试实例已就绪 (CDP http://127.0.0.1:${PORT})"
echo "[discord_login] 请在弹出的窗口里扫码/登录 Discord,确认能看到频道消息后即可。"
echo "[discord_login] 登录态会存进 $PROFILE,worker 之后自动复用。"
