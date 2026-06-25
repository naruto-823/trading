#!/bin/zsh
# 拉起一个带远程调试口(9223)的持久 profile Chrome,停在目标 Discord 频道,供你首次扫码登录。
# 登录态存进 ~/.discord-profile;之后 discord_news_worker 用 connect_over_cdp 连它读消息。
# 跟长桥 .lb-trade-profile / 9222 隔离,互不影响。
export PATH="$PATH:$(npm config get prefix)/bin:$HOME/.local/bin"
PROFILE="$HOME/.discord-profile"
CHROME="/Applications/Google Chrome 2.app/Contents/MacOS/Google Chrome"
PORT=9223
GUILD="1394858797025198150"
CHANNEL="1431922532872880278"
URL="https://discord.com/channels/${GUILD}/${CHANNEL}"

# 没活着就拉起(headful,登录态在 profile 里持久)
if ! curl -s -o /dev/null --max-time 3 "http://127.0.0.1:${PORT}/json/version" 2>/dev/null; then
  rm -f "$PROFILE/SingletonLock" 2>/dev/null
  nohup "$CHROME" --remote-debugging-port=${PORT} --user-data-dir="$PROFILE" \
    --no-first-run --no-default-browser-check "$URL" \
    >/tmp/discord_chrome.log 2>&1 &
  for i in {1..40}; do
    curl -s -o /dev/null --max-time 2 "http://127.0.0.1:${PORT}/json/version" 2>/dev/null && break
    sleep 1
  done
  sleep 4
fi

echo "[discord_login] Chrome 调试实例已就绪 (CDP http://127.0.0.1:${PORT})"
echo "[discord_login] 请在弹出的窗口里扫码/登录 Discord,确认能看到频道消息后即可。"
echo "[discord_login] 登录态会存进 $PROFILE,worker 之后自动复用。"
