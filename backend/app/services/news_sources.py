"""新闻源 fallback 链

Tier 1（专业财经 API，信号最干净）：Finnhub > Alpha Vantage(略，免费 25/day 不够)
Tier 2（通用搜索 API，信号 OK）：Tavily > Brave
Tier 3（无限免费 RSS）：Google News

调用方只看 fetch_news(symbol, name) 一个入口；按顺序尝试，第一个返回非空结果就停。
没配 API key 的 tier 自动跳过。
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 8.0
DEFAULT_LIMIT = 5
MAX_AGE_HOURS = 48

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class NewsItem:
    title: str
    summary: str   # 可能为空（Google News RSS 没有 summary）
    publisher: str
    url: str
    published_at: datetime | None
    source_tier: str  # "finnhub" / "tavily" / "brave" / "google_rss"

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "summary": self.summary,
            "publisher": self.publisher,
            "url": self.url,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "source_tier": self.source_tier,
        }


# ---------- Tier 1: Finnhub ----------

def fetch_finnhub(symbol: str, limit: int = DEFAULT_LIMIT, client: httpx.Client | None = None) -> list[NewsItem]:
    """Finnhub /company-news endpoint。需要 FINNHUB_API_KEY。免费 60 calls/min。"""
    if not settings.finnhub_api_key:
        return []
    raw_symbol = _strip_market_suffix(symbol)
    if not raw_symbol:
        return []

    today = datetime.now(timezone.utc).date()
    frm = (today - timedelta(days=2)).isoformat()
    to = today.isoformat()

    own = client is None
    if own:
        client = httpx.Client(timeout=DEFAULT_TIMEOUT, headers=HTTP_HEADERS)
    try:
        resp = client.get(
            "https://finnhub.io/api/v1/company-news",
            params={"symbol": raw_symbol, "from": frm, "to": to, "token": settings.finnhub_api_key},
        )
        resp.raise_for_status()
        data = resp.json() or []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
        items: list[NewsItem] = []
        for n in data:
            ts = n.get("datetime")
            pub_dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
            if pub_dt and pub_dt < cutoff:
                continue
            items.append(NewsItem(
                title=(n.get("headline") or "").strip(),
                summary=(n.get("summary") or "").strip(),
                publisher=(n.get("source") or "").strip(),
                url=(n.get("url") or "").strip(),
                published_at=pub_dt,
                source_tier="finnhub",
            ))
            if len(items) >= limit:
                break
        return items
    except Exception as exc:
        logger.warning("Finnhub fetch failed for %s: %s", symbol, exc)
        return []
    finally:
        if own:
            client.close()


# ---------- Tier 2: Tavily ----------

def fetch_tavily(query: str, limit: int = DEFAULT_LIMIT, client: httpx.Client | None = None) -> list[NewsItem]:
    """Tavily search API。需要 TAVILY_API_KEY。免费 1000/月。"""
    if not settings.tavily_api_key:
        return []
    own = client is None
    if own:
        client = httpx.Client(timeout=DEFAULT_TIMEOUT, headers=HTTP_HEADERS)
    try:
        resp = client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": settings.tavily_api_key,
                "query": query,
                "search_depth": "basic",
                "topic": "news",
                "max_results": limit,
                "days": 2,
                "include_answer": False,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        items: list[NewsItem] = []
        for r in data.get("results", [])[:limit]:
            pub_dt = None
            if r.get("published_date"):
                try:
                    pub_dt = datetime.fromisoformat(r["published_date"].replace("Z", "+00:00"))
                except Exception:
                    pass
            items.append(NewsItem(
                title=(r.get("title") or "").strip(),
                summary=(r.get("content") or "").strip(),
                publisher=_extract_domain(r.get("url", "")),
                url=(r.get("url") or "").strip(),
                published_at=pub_dt,
                source_tier="tavily",
            ))
        return items
    except Exception as exc:
        logger.warning("Tavily fetch failed for '%s': %s", query, exc)
        return []
    finally:
        if own:
            client.close()


# ---------- Tier 2 alt: Brave Search ----------

def fetch_brave(query: str, limit: int = DEFAULT_LIMIT, client: httpx.Client | None = None) -> list[NewsItem]:
    """Brave Search API news vertical。需要 BRAVE_API_KEY。免费 2000/月。"""
    if not settings.brave_api_key:
        return []
    own = client is None
    if own:
        client = httpx.Client(timeout=DEFAULT_TIMEOUT, headers=HTTP_HEADERS)
    try:
        resp = client.get(
            "https://api.search.brave.com/res/v1/news/search",
            params={"q": query, "count": limit, "freshness": "pd"},  # past day
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": settings.brave_api_key,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        items: list[NewsItem] = []
        for r in (data.get("results") or [])[:limit]:
            pub_dt = None
            if r.get("age"):
                # Brave returns relative "2 hours ago" — skip parsing for now
                pass
            items.append(NewsItem(
                title=(r.get("title") or "").strip(),
                summary=(r.get("description") or "").strip(),
                publisher=(r.get("meta_url", {}).get("netloc") or "").strip(),
                url=(r.get("url") or "").strip(),
                published_at=pub_dt,
                source_tier="brave",
            ))
        return items
    except Exception as exc:
        logger.warning("Brave fetch failed for '%s': %s", query, exc)
        return []
    finally:
        if own:
            client.close()


# ---------- Tier 3: Google News RSS（兜底，无需 key）----------

def fetch_google_rss(symbol: str, name: str | None, limit: int = DEFAULT_LIMIT, client: httpx.Client | None = None) -> list[NewsItem]:
    query, hl, gl = _build_google_query(symbol, name)
    ceid = f"{gl}:{hl.split('-')[0]}"

    own = client is None
    if own:
        client = httpx.Client(timeout=DEFAULT_TIMEOUT, headers=HTTP_HEADERS, follow_redirects=True)
    try:
        resp = client.get(
            "https://news.google.com/rss/search",
            params={"q": query, "hl": hl, "gl": gl, "ceid": ceid},
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
        items: list[NewsItem] = []
        for it in root.findall(".//item"):
            title_el = it.find("title")
            link_el = it.find("link")
            pub_el = it.find("pubDate")
            src_el = it.find("source")
            if title_el is None or link_el is None:
                continue
            pub_dt = None
            if pub_el is not None and pub_el.text:
                try:
                    pub_dt = parsedate_to_datetime(pub_el.text)
                except Exception:
                    pub_dt = None
            if pub_dt and pub_dt < cutoff:
                continue
            items.append(NewsItem(
                title=(title_el.text or "").strip(),
                summary="",  # Google News RSS 没 summary
                publisher=(src_el.text if src_el is not None else "").strip(),
                url=(link_el.text or "").strip(),
                published_at=pub_dt,
                source_tier="google_rss",
            ))
            if len(items) >= limit:
                break
        return items
    except Exception as exc:
        logger.warning("Google News fetch failed for %s: %s", symbol, exc)
        return []
    finally:
        if own:
            client.close()


# ---------- Orchestrator ----------

def fetch_news(
    symbol: str,
    name: str | None = None,
    limit: int = DEFAULT_LIMIT,
    client: httpx.Client | None = None,
) -> list[NewsItem]:
    """按 Tier 1 → 2 → 3 顺序尝试，第一个返回非空就停。
    没配 API key 的 tier 自动跳过。"""
    # Tier 1
    if settings.finnhub_api_key:
        items = fetch_finnhub(symbol, limit, client)
        if items:
            return items
    # Tier 2
    query = _build_search_query(symbol, name)
    if settings.tavily_api_key:
        items = fetch_tavily(query, limit, client)
        if items:
            return items
    if settings.brave_api_key:
        items = fetch_brave(query, limit, client)
        if items:
            return items
    # Tier 3（永远可用）
    return fetch_google_rss(symbol, name, limit, client)


def available_sources() -> list[dict]:
    """供 /api/system/news-sources/status 用"""
    return [
        {"name": "finnhub", "tier": 1, "configured": bool(settings.finnhub_api_key)},
        {"name": "tavily", "tier": 2, "configured": bool(settings.tavily_api_key)},
        {"name": "brave", "tier": 2, "configured": bool(settings.brave_api_key)},
        {"name": "google_rss", "tier": 3, "configured": True},
    ]


# ---------- helpers ----------

def _strip_market_suffix(symbol: str) -> str:
    """AAPL.US → AAPL；700.HK → 0700.HK（Finnhub 用 0700.HK 格式）"""
    if symbol.endswith(".US"):
        return symbol[:-3]
    if symbol.endswith(".HK"):
        code = symbol[:-3]
        try:
            return f"{int(code):04d}.HK"
        except ValueError:
            return symbol
    return symbol


def _build_google_query(symbol: str, name: str | None) -> tuple[str, str, str]:
    """构造 Google News 查询：US 用英文，HK 用中文（如有中文名）"""
    if symbol.endswith(".US"):
        return f"{symbol[:-3]} stock", "en-US", "US"
    if symbol.endswith(".HK"):
        if name and any("一" <= ch <= "鿿" for ch in name):
            return name, "zh-Hant", "HK"
        return f"{symbol[:-3]}.HK", "zh-Hant", "HK"
    return name or symbol, "en-US", "US"


def _build_search_query(symbol: str, name: str | None) -> str:
    """给通用搜索 API 用（Tavily/Brave）"""
    base = symbol.split(".")[0]
    if name:
        return f"{name} ({base}) stock news"
    return f"{base} stock news"


def _extract_domain(url: str) -> str:
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""
