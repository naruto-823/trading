"""连 Discord(登录态)抓个股新闻 bot 的「24h个股新闻总结」→ 共享 news_pipeline。

为啥这样设计:
- Discord 第三方付费群,官方 API 不开放(非管理员加不了 bot);浏览器登录态读 DOM
  是风险最低的现实路子(只读、不发、不冒充 bot)。见设计 spec。
- 用持久 profile 的 headful 调试 Chrome(automation/discord_login.sh 拉起、你首次扫码),
  worker 用 Playwright connect_over_cdp 连它。headful 避开 Discord 的 headless 检测。
- 浏览器内轮询 + message-id diff 抓新消息(对标 jin10);bot 整点才发,poll 10s 足够。

重叠去重(关键):bot 每整点发「24h滚动总结」,相邻整点大量重复同一批新闻。
所以去重粒度 = 单条新闻内容(ticker+正文),不是 Discord 消息;且与 Jin10 共用
event_notification 表 → 金十先报、Discord 再现也不双推。

默认关,settings.discord_news_realtime=True 才启。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from app.config import settings
from app.services.discord_parser import parse_news_summary
from app.services.news_pipeline import process_news_item
from app.services.notify import send_bark

logger = logging.getLogger(__name__)

_browser_task: asyncio.Task | None = None
_state: dict[str, Any] = {
    "enabled": False,
    "connected": False,
    "received": 0,      # 收到的 bot 消息数
    "items": 0,         # 拆出的单条新闻数
    "filtered": 0,
    "deduped": 0,
    "scored_low": 0,
    "escalated": 0,
    "fired": 0,
    "last_received_at": None,
    "last_fired_at": None,
    "last_error": None,
    "login_lost": False,
    "reconnects": 0,
}

PAGE_REFRESH_INTERVAL_S = 6 * 3600

# 注入到 Discord 页面的 hook:poll 消息列表底部 + message-id diff。
# NOTE: Discord 选择器需在真实登录态页面核对;以下为最可能结构,登录后据实调整。
#   消息项: li[id^="chat-messages-"](id 形如 chat-messages-<channel>-<msgid>)
#   正文:   该 li 的 innerText(bot 的总结正文都在里面)
HOOK_SCRIPT = r"""
() => {
    if (window.__discordHookInstalled) return true;

    function extract(el) {
        let id = '';
        const m = (el.id || '').match(/chat-messages-(?:\d+-)?(\d+)/);
        id = m ? m[1] : (el.id || '');
        let text = '';
        try { text = (el.innerText || '').trim(); } catch (e) {}
        return { id: id, text: text, ts: Date.now() };
    }

    let lastSeenIds = new Set();
    let firstScan = true;

    function poll() {
        const items = document.querySelectorAll('li[id^="chat-messages-"]');
        const currentIds = [];
        const newOnes = [];
        for (let i = 0; i < items.length; i++) {
            const data = extract(items[i]);
            if (!data.id) continue;
            currentIds.push(data.id);
            if (!lastSeenIds.has(data.id)) newOnes.push(data);
        }
        if (firstScan) {
            currentIds.forEach((id) => lastSeenIds.add(id));
            firstScan = false;
            return;
        }
        for (const data of newOnes) {
            if (data.text && data.text.length > 5) {
                try { window.__onNewDiscordMsg(data); } catch (e) {}
            }
            lastSeenIds.add(data.id);
        }
        if (lastSeenIds.size > 500) {
            lastSeenIds = new Set(Array.from(lastSeenIds).slice(-200));
        }
    }

    poll();
    window.__discordPoller = setInterval(poll, %POLL_MS%);
    window.__discordHookInstalled = true;
    return true;
}
"""


async def _process_message_async(msg: dict[str, Any]) -> None:
    """从 page 收到一条 bot 消息 → 拆条 → 逐条过共享管道(IO 走线程池)。"""
    _state["received"] += 1
    _state["last_received_at"] = datetime.utcnow().isoformat() + "Z"
    try:
        await asyncio.to_thread(_process_message_sync, msg.get("text") or "")
    except Exception as exc:
        logger.exception("discord-news process failed: %s", exc)
        _state["last_error"] = str(exc)[:200]


def _process_message_sync(text: str) -> None:
    items = parse_news_summary(text)
    for it in items:
        _state["items"] += 1
        ticker = it.get("ticker")
        body = it.get("text") or ""
        if not body:
            continue
        # 去重种子 = ticker + 正文前120字(防整点重叠刷屏),与 Jin10 共用 event_notification
        dedup_seed = f"{ticker or '?'}|{body[:120]}"
        result = process_news_item(
            body,
            source="discord",
            dedup_seed=dedup_seed,
            known_tickers=[ticker] if ticker else None,
            source_label="Discord",
            state=_state,
        )
        if result == "sent":
            _state["last_fired_at"] = datetime.utcnow().isoformat() + "Z"


async def _find_discord_page(browser, channel: str):
    """在已连接的浏览器里找到停在目标频道的页面;找不到返回 None。"""
    for ctx in browser.contexts:
        for page in ctx.pages:
            try:
                if channel in (page.url or ""):
                    return page
            except Exception:
                continue
    # 没有就用第一个 context 开一页导航过去
    if browser.contexts and browser.contexts[0].pages:
        return browser.contexts[0].pages[0]
    return None


def _looks_logged_out(text: str) -> bool:
    """粗判登录态丢失(被踢回登录/二步验证页)。"""
    markers = ["两步验证", "登录", "Log In", "验证码", "QR", "扫码"]
    return any(m in text for m in markers) and "chat-messages-" not in text


async def _run_loop() -> None:
    """长期跑:连 CDP → 注入 hook → 监听新消息;失败自动重连。"""
    from playwright.async_api import async_playwright

    channels = settings.discord_channels
    if not channels:
        logger.warning("discord-news: 无频道配置,退出")
        return
    target = channels[0]  # 第一期只接一个频道
    guild, channel = target["guild"], target["channel"]
    url = f"https://discord.com/channels/{guild}/{channel}"
    hook = HOOK_SCRIPT.replace("%POLL_MS%", str(settings.discord_poll_interval_s * 1000))

    async with async_playwright() as p:
        while True:
            browser = None
            try:
                logger.info("discord-news: connecting CDP %s", settings.discord_cdp_url)
                browser = await p.chromium.connect_over_cdp(settings.discord_cdp_url)
                page = await _find_discord_page(browser, channel)
                if page is None:
                    raise RuntimeError("没有可用页面;请先跑 discord_login.sh 并登录")

                if channel not in (page.url or ""):
                    await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    await asyncio.sleep(5)

                body_text = await page.evaluate("document.body.innerText")
                if _looks_logged_out(body_text):
                    _state["login_lost"] = True
                    _state["last_error"] = "登录态丢失"
                    send_bark(
                        "🔑 Discord 登录态过期",
                        "个股新闻抓取停了,请在 .discord-profile 窗口重新扫码登录",
                        level="timeSensitive", group="market-events",
                    )
                    logger.warning("discord-news: 登录态丢失,等待重登")
                    await asyncio.sleep(300)
                    continue
                _state["login_lost"] = False

                await page.expose_function("__onNewDiscordMsg", _process_message_async)
                installed = await page.evaluate(hook)
                if not installed:
                    raise RuntimeError("hook install failed")

                _state["connected"] = True
                _state["last_error"] = None
                logger.info("discord-news: hook installed on %s", channel)

                started_at = datetime.utcnow()
                while True:
                    await asyncio.sleep(30)
                    if (datetime.utcnow() - started_at).total_seconds() > PAGE_REFRESH_INTERVAL_S:
                        logger.info("discord-news: 6h refresh")
                        break
                    ok = await page.evaluate("window.__discordHookInstalled === true")
                    if not ok:
                        logger.warning("discord-news: hook lost")
                        break
            except asyncio.CancelledError:
                logger.info("discord-news: cancelled")
                raise
            except Exception as exc:
                _state["last_error"] = str(exc)[:200]
                logger.error("discord-news loop error: %s", exc, exc_info=True)
            finally:
                _state["connected"] = False
                # 连的是外部调试 Chrome,不关浏览器,只断开连接
                if browser is not None:
                    try:
                        await browser.close()
                    except Exception:
                        pass

            _state["reconnects"] += 1
            await asyncio.sleep(30)


def start() -> None:
    global _browser_task
    if not settings.discord_news_realtime:
        logger.info("discord-news: disabled (set DISCORD_NEWS_REALTIME=1 to enable)")
        return
    if _browser_task and not _browser_task.done():
        return
    _state["enabled"] = True
    _browser_task = asyncio.create_task(_run_loop())
    logger.info("discord-news: started")


async def stop() -> None:
    global _browser_task
    if _browser_task:
        _browser_task.cancel()
        try:
            await _browser_task
        except asyncio.CancelledError:
            pass
        _browser_task = None


def status() -> dict[str, Any]:
    return dict(_state)
