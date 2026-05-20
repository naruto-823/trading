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
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

_DIR_EMOJI = {"bullish": "📈", "bearish": "📉", "neutral": ""}

from app.config import settings
from app.db import SessionLocal
from app.models.event_notification import EventNotification
from app.services.macro_feed import MacroFlash
from app.services.macro_pusher import matches_keywords
from app.services.notify import send_bark
from app.services.relevance_scorer import score_relevance
from app.services.debate_queue import submit_debate
from app.services.debate_scorer import should_escalate

logger = logging.getLogger(__name__)

_browser_task: asyncio.Task | None = None
_state: dict[str, Any] = {
    "enabled": False,
    "connected": False,
    "received": 0,
    "filtered": 0,
    "deduped": 0,
    "scored_low": 0,
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
    content = (flash.get("text") or "").strip()
    if not content:
        return

    item = MacroFlash(
        time=datetime.now(timezone.utc),
        title=content[:80],
        content=content,
        importance=5 if flash.get("is_important") else 3,
        source="jin10",
        tags=[flash["relevance"]] if flash.get("relevance") else [],
    )

    if not matches_keywords(item):
        _state["filtered"] += 1
        return

    # 双 hash 去重：跟 macro_pusher 共享 event_notification 不会双推
    dedup_key = flash.get("id") or content[:120]
    h = hashlib.sha256(f"jin10-rt|{dedup_key}".encode()).hexdigest()[:32]
    h_macro_pusher = hashlib.sha256(
        f"macro|jin10|{content[:120].lower()}".encode()
    ).hexdigest()[:32]

    db = SessionLocal()
    try:
        if db.query(EventNotification).filter(
            EventNotification.event_hash.in_([h, h_macro_pusher])
        ).first():
            _state["deduped"] += 1
            return

        # Quick Assess：多维度评分
        scoring = score_relevance(content)
        score = scoring["score"]
        affected = scoring["affected_tickers"]
        affected_json = json.dumps(affected, ensure_ascii=False) if affected else None
        symbol = affected[0] if affected else None

        common_kwargs = dict(
            id=uuid.uuid4().hex,
            event_hash=h,
            notified_at=datetime.utcnow(),
            symbol=symbol,
            source_title=f"[jin10-realtime] {content[:200]}",
            relevance=scoring["relevance"],
            relevance_score=score,
            relevance_reason=scoring["reason"],
            sentiment=scoring["sentiment"],
            direction=scoring["direction"],
            confidence=scoring["confidence"],
            affected_tickers_json=affected_json,
        )

        # 两阶段门控:高 stakes 快讯升级到完整辩论(异步,不在此处推送)
        if should_escalate(scoring, item.importance):
            rec = EventNotification(
                **common_kwargs,
                importance="high" if flash.get("is_important") else "medium",
                title=content[:200],
                body=content[:500],
                push_status="debating",
                push_error=None,
            )
            db.add(rec)
            db.commit()
            submit_debate(rec.id)
            logger.info("jin10-browser escalated to debate: %s", content[:60])
            return

        if score < settings.relevance_threshold:
            rec = EventNotification(
                **common_kwargs,
                importance="high" if flash.get("is_important") else "medium",
                title=content[:200],
                body=content[:500],
                push_status="skipped_low_relevance",
                push_error=None,
            )
            db.add(rec)
            db.commit()
            _state["scored_low"] += 1
            logger.info("jin10-browser skipped [score=%d %s dir=%s]: %s",
                        score, scoring["relevance"], scoring["direction"], content[:60])
            return

        # 高分：推 Bark，title 加 emoji + ticker
        marker = "⚡" if flash.get("is_important") else "📡"
        dir_icon = _DIR_EMOJI.get(scoring["direction"], "")
        ticker_part = f"{affected[0]} " if affected else ""
        title = f"{marker}{dir_icon}[金十] {ticker_part}{content[:25]}{'…' if len(content) > 25 else ''}"

        # body 头部加一行 sentiment / direction / confidence 标签（不是 neutral 才加）
        body_lines = []
        if scoring["sentiment"] != "neutral" or scoring["direction"] != "neutral":
            sent = {"positive": "利好", "negative": "利空", "neutral": "中性"}[scoring["sentiment"]]
            dir_label = {"bullish": "看涨", "bearish": "看跌", "neutral": "中性"}[scoring["direction"]]
            body_lines.append(f"{sent} · {dir_label} · 可信度 {scoring['confidence']}%")
        body_lines.append(content)
        body = "\n".join(body_lines)[:500]

        level = "timeSensitive" if flash.get("is_important") else "active"
        result = send_bark(title, body, level=level, group="market-events", sound="chime")

        rec = EventNotification(
            **common_kwargs,
            importance="high" if flash.get("is_important") else "medium",
            title=content[:200],
            body=body,
            push_status="sent" if result["ok"] else "failed",
            push_error=None if result["ok"] else str(result["detail"])[:500],
        )
        db.add(rec)
        db.commit()

        if result["ok"]:
            _state["fired"] += 1
            _state["last_fired_at"] = datetime.utcnow().isoformat() + "Z"
            _state["last_fired_title"] = content[:80]
            logger.info("jin10-browser fired [score=%d dir=%s]: %s",
                        score, scoring["direction"], content[:80])
    finally:
        db.close()


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
