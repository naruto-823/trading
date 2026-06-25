"""Playwright 跑 headless chromium → 实时拿金十快讯推送

为啥这样设计：
- 金十 wss-flash-* 端点 payload 是自定义混淆，反向工程脆弱
- jin10.com 自己的 JS 完美解码 → 跑无头浏览器让他们的 JS 帮我们解码
- 浏览器内 2s poll + ID diff 比 MutationObserver 更鲁棒（Vue 整体 re-render 时观察器会漏抓）

成本：~150MB 内存 + 一个 chromium headless 进程常驻
和 MCP 1min 轮询并存：通过 event_notification 表去重，不会双推
默认关，settings.jin10_browser_realtime=True 才启
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime
from typing import Any

from app.config import settings
from app.services.news_pipeline import process_news_item

logger = logging.getLogger(__name__)

_browser_task: asyncio.Task | None = None
_state: dict[str, Any] = {
    "enabled": False,
    "connected": False,
    "received": 0,
    "filtered": 0,
    "deduped": 0,
    "scored_low": 0,
    "escalated": 0,
    "fired": 0,
    "last_received_at": None,
    "last_fired_at": None,
    "last_fired_title": None,
    "last_error": None,
    "reconnects": 0,
}

PAGE_REFRESH_INTERVAL_S = 6 * 3600  # 6h 刷一次页避免 Vue store 累积


# ---- 注入到 page 的 JS：2s poll + id diff ----
HOOK_SCRIPT = r"""
() => {
    if (window.__jin10HookInstalled) return true;

    function extract(el) {
        const shareLink = el.querySelector('a[href*="flash.jin10.com/detail/"]');
        const detailId = shareLink ? (shareLink.href.match(/detail\/(\d+)/) || [])[1] : null;
        let text = '';
        try { text = (el.innerText || '').replace(/\s+/g, ' ').trim(); } catch (e) {}
        return {
            id: detailId,
            text: text,
            is_important: el.classList.contains('is-important'),
            relevance: el.dataset ? el.dataset.relevance : '',
            ts: Date.now(),
        };
    }

    let lastSeenIds = new Set();
    let firstScan = true;

    function poll() {
        const items = document.querySelectorAll('.jin-flash-item');
        const currentIds = [];
        const newOnes = [];
        for (let i = 0; i < items.length; i++) {
            const data = extract(items[i]);
            if (!data.id) continue;
            currentIds.push(data.id);
            if (!lastSeenIds.has(data.id)) newOnes.push(data);
        }
        // 首次：当 baseline，不推
        if (firstScan) {
            currentIds.forEach((id) => lastSeenIds.add(id));
            firstScan = false;
            return;
        }
        for (const data of newOnes) {
            if (data.text && data.text.length > 5) {
                try { window.__onNewFlash(data); } catch (e) {}
            }
            lastSeenIds.add(data.id);
        }
        if (lastSeenIds.size > 500) {
            lastSeenIds = new Set(Array.from(lastSeenIds).slice(-200));
        }
    }

    poll();
    window.__jin10Poller = setInterval(poll, 2000);
    window.__jin10HookInstalled = true;
    return true;
}
"""


async def _process_flash_async(flash: dict[str, Any]) -> None:
    """从 page 收到一条 flash → filter → dedup → 推 Bark（IO 走线程池）"""
    _state["received"] += 1
    _state["last_received_at"] = datetime.utcnow().isoformat() + "Z"
    try:
        await asyncio.to_thread(_process_flash_sync, flash)
    except Exception as exc:
        logger.exception("jin10-browser process failed: %s", exc)
        _state["last_error"] = str(exc)[:200]


def _process_flash_sync(flash: dict[str, Any]) -> None:
    """委托给共享管道处理一条金十快讯。

    金十特有的部分:① 跨源去重 hash(跟 macro_pusher 1min 轮询共享,不双推);
    ② 来源标记 jin10-rt / 标题前缀「金十」/ tags 取 dataset.relevance。
    其余评分/门控/推送全在 news_pipeline.process_news_item。
    """
    content = (flash.get("text") or "").strip()
    if not content:
        return

    h_macro_pusher = hashlib.sha256(
        f"macro|jin10|{content[:120].lower()}".encode()
    ).hexdigest()[:32]

    result = process_news_item(
        content,
        source="jin10-rt",
        dedup_seed=flash.get("id") or content[:120],
        is_important=bool(flash.get("is_important")),
        cross_source_hashes=[h_macro_pusher],
        source_label="金十",
        tags=[flash["relevance"]] if flash.get("relevance") else None,
        state=_state,
    )

    if result == "sent":
        _state["last_fired_at"] = datetime.utcnow().isoformat() + "Z"
        _state["last_fired_title"] = content[:80]


async def _run_browser_loop() -> None:
    """长期跑：失败自动重连"""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        while True:
            browser = None
            try:
                logger.info("jin10-browser: launching chromium…")
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
                )
                ctx = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 720},
                )
                page = await ctx.new_page()

                await page.expose_function("__onNewFlash", _process_flash_async)

                await page.goto("https://www.jin10.com/", timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_selector(".jin-flash-item", timeout=20000)
                await asyncio.sleep(2)  # 等 Vue 渲染完

                installed = await page.evaluate(HOOK_SCRIPT)
                if not installed:
                    raise RuntimeError("hook install failed")

                _state["connected"] = True
                _state["last_error"] = None
                logger.info("jin10-browser: hook installed, listening for new flashes…")

                page_started_at = datetime.utcnow()
                while True:
                    await asyncio.sleep(30)
                    if (datetime.utcnow() - page_started_at).total_seconds() > PAGE_REFRESH_INTERVAL_S:
                        logger.info("jin10-browser: 6h refresh")
                        break
                    try:
                        ok = await page.evaluate("window.__jin10HookInstalled === true")
                        if not ok:
                            logger.warning("jin10-browser: hook lost")
                            break
                    except Exception:
                        logger.warning("jin10-browser: page eval failed")
                        break
            except asyncio.CancelledError:
                logger.info("jin10-browser: cancelled")
                raise
            except Exception as exc:
                _state["last_error"] = str(exc)[:200]
                logger.error("jin10-browser loop error: %s", exc, exc_info=True)
            finally:
                _state["connected"] = False
                if browser is not None:
                    try:
                        await browser.close()
                    except Exception:
                        pass

            _state["reconnects"] += 1
            await asyncio.sleep(30)


def start() -> None:
    global _browser_task
    if not settings.jin10_browser_realtime:
        logger.info("jin10-browser: disabled (set JIN10_BROWSER_REALTIME=1 to enable)")
        return
    if _browser_task and not _browser_task.done():
        return
    _state["enabled"] = True
    _browser_task = asyncio.create_task(_run_browser_loop())
    logger.info("jin10-browser: started")


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
