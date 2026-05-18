"""中文宏观快讯流（macro feed）

跟 news_sources 的区别：
- news_sources 是 per-symbol（"MSFT 的新闻"）
- macro_feed 是 global 宏观流（FOMC / CPI / 地缘 / 油价 等），不绑定 symbol

接 3 个中文源（互补，都不需要 key）：
- 金十数据 jin10.com（flash_newest.js）
- 财联社 cls.cn (updateTelegraphList)
- 华尔街见闻 wallstcn (apiv1/content/lives)

event_watcher 在 detect_events 时同时拉这些，喂给 LLM 让它判断"是否对用户持仓有重大影响"。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 8.0
DEFAULT_LIMIT_PER_SOURCE = 15
MACRO_MAX_AGE_HOURS = 6  # 宏观快讯保鲜期短，> 6h 基本失效

HK_TZ = timezone(timedelta(hours=8))


@dataclass
class MacroFlash:
    time: datetime           # UTC
    title: str               # 简短标题
    content: str             # 完整内容
    importance: int          # 1-5，越高越重要（不同源映射规则）
    source: str              # "jin10" / "cailianshe" / "wallstcn"
    tags: list[str]          # 可选

    def to_dict(self) -> dict:
        return {
            "time": self.time.isoformat(),
            "title": self.title,
            "content": self.content,
            "importance": self.importance,
            "source": self.source,
            "tags": self.tags,
        }


# ---------- 金十数据 ----------

JIN10_JS_RE = re.compile(r"var\s+newest\s*=\s*(\[.*?\]);?\s*$", re.DOTALL)


def fetch_jin10(limit: int = DEFAULT_LIMIT_PER_SOURCE) -> list[MacroFlash]:
    """金十快讯。配了 MCP token 走官方 MCP（结构化、稳定），否则解析公开 JS 文件兜底"""
    from app.services.mcp_jin10 import is_configured as mcp_ready, list_flash as mcp_list_flash

    if mcp_ready():
        try:
            return _fetch_jin10_via_mcp(limit, mcp_list_flash)
        except Exception as exc:
            logger.warning("Jin10 MCP failed, fallback to JS parse: %s", exc)
    return _fetch_jin10_legacy_js(limit)


def _fetch_jin10_via_mcp(limit: int, mcp_list_flash) -> list[MacroFlash]:
    """通过 MCP 拿结构化快讯。MCP 不提供 importance 字段，整体当 imp=2 处理；
    含特定关键词（重要/紧急/突发/Fed/CPI/FOMC）的提升到 4。
    """
    raw = mcp_list_flash(limit=limit * 2)  # 多拉一些，下面再裁
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MACRO_MAX_AGE_HOURS)
    important_kw = ("【重要】", "【紧急】", "突发", "fed", "fomc", "cpi", "ppi", "非农", "美联储", "鲍威尔", "央行")

    results: list[MacroFlash] = []
    for it in raw:
        # MCP time 是 ISO 8601 (e.g. 2026-05-18T19:32:25+08:00)
        try:
            t = datetime.fromisoformat(it["time"]).astimezone(timezone.utc)
        except Exception:
            continue
        if t < cutoff:
            continue
        content = (it.get("content") or "").strip()
        title = (it.get("title") or "").strip() or content[:80]
        if not content and not title:
            continue
        # 启发式 importance：含关键词标 4，否则 2
        text_lower = (title + " " + content).lower()
        importance = 4 if any(kw in text_lower for kw in important_kw) else 2
        results.append(MacroFlash(
            time=t,
            title=title,
            content=content or title,
            importance=importance,
            source="jin10",
            tags=[],
        ))
        if len(results) >= limit:
            break
    return results


def _fetch_jin10_legacy_js(limit: int) -> list[MacroFlash]:
    """金十 flash_newest.js（JS 文件，正则提取 JSON 数组）—— MCP 不可用时兜底"""
    try:
        resp = httpx.get(
            "https://www.jin10.com/flash_newest.js",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Referer": "https://www.jin10.com/",
            },
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        text = resp.text.strip()
        # 去掉 var newest = ; 包装
        m = JIN10_JS_RE.search(text)
        if not m:
            # 兜底：可能没分号结尾
            if "var newest" in text and "[" in text:
                start = text.index("[")
                end = text.rindex("]") + 1
                raw_json = text[start:end]
            else:
                logger.warning("Jin10 response doesn't match expected JS format")
                return []
        else:
            raw_json = m.group(1)

        items = json.loads(raw_json)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MACRO_MAX_AGE_HOURS)
        results: list[MacroFlash] = []
        for it in items:
            t_str = it.get("time")
            try:
                t = datetime.strptime(t_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=HK_TZ).astimezone(timezone.utc)
            except Exception:
                continue
            if t < cutoff:
                continue
            data = it.get("data", {})
            content = (data.get("content") or "").strip()
            title = (data.get("title") or "").strip() or content[:80]
            if not content and not title:
                continue
            # 金十的 important 字段：0/1（important=1 是重要新闻）
            importance = 4 if it.get("important") == 1 else 2
            results.append(MacroFlash(
                time=t,
                title=title,
                content=content or title,
                importance=importance,
                source="jin10",
                tags=[],
            ))
            if len(results) >= limit:
                break
        return results
    except Exception as exc:
        logger.warning("Jin10 fetch failed: %s", exc)
        return []


# ---------- 财联社 cls.cn ----------

def fetch_cailianshe(limit: int = DEFAULT_LIMIT_PER_SOURCE) -> list[MacroFlash]:
    try:
        resp = httpx.get(
            "https://www.cls.cn/nodeapi/updateTelegraphList",
            params={"app": "CailianpressWeb", "os": "web", "sv": "7.7.5", "last_time": 0, "rn": limit * 2},
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            },
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        roll = (data.get("data") or {}).get("roll_data") or data.get("data") or []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MACRO_MAX_AGE_HOURS)
        results: list[MacroFlash] = []
        for it in roll:
            ts = it.get("ctime") or it.get("modified_time")
            try:
                t = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except Exception:
                continue
            if t < cutoff:
                continue
            title = (it.get("title") or "").strip()
            content = (it.get("brief") or it.get("content") or "").strip()
            if not title and not content:
                continue
            # 财联社的 is_red=1 是红色标重（重要）
            importance = 4 if it.get("is_red") else 2
            results.append(MacroFlash(
                time=t,
                title=title or content[:80],
                content=content or title,
                importance=importance,
                source="cailianshe",
                tags=[],
            ))
            if len(results) >= limit:
                break
        return results
    except Exception as exc:
        logger.warning("Cailianshe fetch failed: %s", exc)
        return []


# ---------- 华尔街见闻 wallstcn ----------

def fetch_wallstcn(limit: int = DEFAULT_LIMIT_PER_SOURCE) -> list[MacroFlash]:
    try:
        resp = httpx.get(
            "https://api-one.wallstcn.com/apiv1/content/lives",
            params={"channel": "global-channel", "client": "pc", "limit": limit * 2},
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            },
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        items = (resp.json().get("data") or {}).get("items") or []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MACRO_MAX_AGE_HOURS)
        results: list[MacroFlash] = []
        for it in items:
            ts = it.get("display_time") or it.get("created_at")
            try:
                t = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except Exception:
                continue
            if t < cutoff:
                continue
            content = (it.get("content_text") or "").strip()
            if not content:
                continue
            title = content[:60] + ("…" if len(content) > 60 else "")
            # 华尔街见闻的 score: 1=普通，2=重要，3=非常重要
            score = it.get("score", 1)
            importance = {1: 2, 2: 4, 3: 5}.get(score, 2)
            results.append(MacroFlash(
                time=t,
                title=title,
                content=content,
                importance=importance,
                source="wallstcn",
                tags=[],
            ))
            if len(results) >= limit:
                break
        return results
    except Exception as exc:
        logger.warning("Wallstcn fetch failed: %s", exc)
        return []


# ---------- 合并 ----------

def fetch_macro_news(
    min_importance: int = 2,
    hours_back: int = 4,
    limit_per_source: int = 8,
) -> list[MacroFlash]:
    """从所有源拉，按时间倒序合并，按 importance 过滤。
    默认 min_importance=2（=源默认级别）—— 让 LLM 看更多原始数据自己判断；
    源标记的 high importance（is_red / score=3 / important=1）会变成 4-5。
    每源限 8 条 = 总共最多 24 条进 LLM，token 可控。
    """
    all_items: list[MacroFlash] = []
    for fetcher, name in [
        (fetch_jin10, "jin10"),
        (fetch_cailianshe, "cailianshe"),
        (fetch_wallstcn, "wallstcn"),
    ]:
        try:
            items = fetcher(limit=limit_per_source)
            all_items.extend(items)
            logger.debug("macro_feed %s: %d items", name, len(items))
        except Exception as exc:
            logger.warning("macro_feed %s failed: %s", name, exc)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    filtered = [
        x for x in all_items
        if x.importance >= min_importance and x.time >= cutoff
    ]
    # 按时间倒序
    filtered.sort(key=lambda x: x.time, reverse=True)
    return filtered


def available_macro_sources() -> list[dict]:
    """用于 /api/system/news-sources 显示"""
    from app.services.mcp_jin10 import is_configured as jin10_mcp_ready
    return [
        {
            "name": "jin10",
            "configured": True,
            "category": "macro_zh",
            "mode": "MCP（结构化）" if jin10_mcp_ready() else "JS 解析（兜底）",
        },
        {"name": "cailianshe", "configured": True, "category": "macro_zh", "mode": "公开 API"},
        {"name": "wallstcn", "configured": True, "category": "macro_zh", "mode": "公开 API"},
    ]
