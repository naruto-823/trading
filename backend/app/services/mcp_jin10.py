"""金十 MCP client（轻量 JSON-RPC over HTTP，无第三方依赖）

MCP 协议 stateful：每个会话需 initialize → notifications/initialized → tools/call。
Server 响应是 SSE 格式（`event: message\\ndata: {...}\\n\\n`）。

Session 管理策略：per-call 重新 init（开销 ~200ms），换稳定性。
3 min 调一次完全可接受，省去 session 过期处理。
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0


def is_configured() -> bool:
    return bool(settings.jin10_mcp_token)


def _parse_sse(text: str) -> dict | None:
    """从 SSE 响应里提取 first data 行的 JSON"""
    for line in text.split("\n"):
        if line.startswith("data: "):
            try:
                return json.loads(line[6:])
            except json.JSONDecodeError:
                return None
    return None


def _build_headers(session_id: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {settings.jin10_mcp_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return headers


def _open_session(client: httpx.Client) -> str:
    """两步握手，返回 session_id"""
    init_resp = client.post(
        settings.jin10_mcp_url,
        headers=_build_headers(),
        json={
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 1,
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ai-trading", "version": "0.1"},
            },
        },
    )
    init_resp.raise_for_status()
    sid = init_resp.headers.get("mcp-session-id")
    if not sid:
        raise RuntimeError("Jin10 MCP did not return session_id")

    # 通知 server "我已就绪"（无响应）
    client.post(
        settings.jin10_mcp_url,
        headers=_build_headers(sid),
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    return sid


def _call_tool(client: httpx.Client, session_id: str, name: str, arguments: dict[str, Any] | None = None) -> Any:
    """tools/call。返回 result.content[0].text 解析后的 dict（金十都返回 JSON 文本块）"""
    resp = client.post(
        settings.jin10_mcp_url,
        headers=_build_headers(session_id),
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 100,
            "params": {"name": name, "arguments": arguments or {}},
        },
    )
    resp.raise_for_status()
    data = _parse_sse(resp.text)
    if not data:
        raise RuntimeError(f"Jin10 MCP tools/call({name}) returned no parseable body")
    if "error" in data:
        raise RuntimeError(f"Jin10 MCP error: {data['error']}")

    result = data.get("result", {})
    content = result.get("content") or []
    if not content:
        return {}
    first = content[0]
    if first.get("type") == "text":
        try:
            return json.loads(first["text"])
        except json.JSONDecodeError:
            return {"raw": first["text"]}
    return first


# ---------- 高层封装：list_flash + list_calendar ----------

def list_flash(limit: int = 30) -> list[dict]:
    """返回 [{time, title, content, url}]（按时间倒序）。失败返回空列表。
    limit 是想要的最大条数；MCP 单次约 20 条，需要分页就追加。
    """
    if not is_configured():
        return []

    items: list[dict] = []
    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            sid = _open_session(client)
            cursor = ""
            for _ in range(5):  # 最多翻 5 页（约 100 条），防止无限循环
                args = {"cursor": cursor} if cursor else {}
                data = _call_tool(client, sid, "list_flash", args)
                page = data.get("data") or {}
                items.extend(page.get("items") or [])
                if len(items) >= limit or not page.get("has_more"):
                    break
                cursor = page.get("next_cursor") or ""
                if not cursor:
                    break
    except Exception as exc:
        logger.warning("Jin10 MCP list_flash failed: %s", exc)
        return []
    return items[:limit]


def list_calendar() -> list[dict]:
    """本周财经日历。返回 [{pub_time, title, star, actual, consensus, previous, ...}]。"""
    if not is_configured():
        return []
    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            sid = _open_session(client)
            data = _call_tool(client, sid, "list_calendar", {})
            return data.get("data") or []
    except Exception as exc:
        logger.warning("Jin10 MCP list_calendar failed: %s", exc)
        return []


# ---------- Session 复用 + 搜索接口 ----------

class _JinSession:
    """共享 session 给多次 tool 调用，省 init 开销"""

    def __init__(self, client: httpx.Client, session_id: str):
        self.client = client
        self.session_id = session_id

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        return _call_tool(self.client, self.session_id, name, arguments)

    def search_flash(self, keyword: str, limit: int = 10) -> list[dict]:
        data = self.call("search_flash", {"keyword": keyword})
        items = (data.get("data") or {}).get("items") or []
        return items[:limit]

    def search_news(self, keyword: str, limit: int = 5) -> list[dict]:
        data = self.call("search_news", {"keyword": keyword})
        items = (data.get("data") or {}).get("items") or []
        return items[:limit]


@contextmanager
def jin10_session():
    """with jin10_session() as s: s.search_flash(...) — 一轮多次调用共享 session"""
    if not is_configured():
        yield None
        return
    client = httpx.Client(timeout=DEFAULT_TIMEOUT)
    try:
        sid = _open_session(client)
        yield _JinSession(client, sid)
    except Exception as exc:
        logger.warning("Jin10 MCP session failed: %s", exc)
        yield None
    finally:
        client.close()


# ---------- 持仓 ticker → 中文搜索关键词映射 ----------

# 把 longbridge symbol 映射到金十常用中文搜索词
# 没在映射里的：US 股回退到 ticker base（如 NVDA）；HK 股用 longbridge 提供的中文 name
TICKER_ZH_KEYWORDS: dict[str, str] = {
    "AAPL.US": "苹果",
    "MSFT.US": "微软",
    "GOOG.US": "谷歌",
    "GOOGL.US": "谷歌",
    "META.US": "Meta",
    "TSLA.US": "特斯拉",
    "NVDA.US": "英伟达",
    "AMZN.US": "亚马逊",
    "TSM.US": "台积电",
    "INTC.US": "英特尔",
    "AMD.US": "AMD",
    "BABA.US": "阿里巴巴",
    "PDD.US": "拼多多",
    "JD.US": "京东",
    "MU.US": "美光",
    "NFLX.US": "奈飞",
    "ARM.US": "ARM",
    "AVGO.US": "博通",
    "ORCL.US": "甲骨文",
    "CRM.US": "Salesforce",
    # ETF 不适合中文搜，返回 None 跳过
    "QQQ.US": "",
    "SPY.US": "",
    "VOO.US": "",
    "IWM.US": "",
}


def get_search_keyword(symbol: str, name: str | None) -> str:
    """决定金十搜索用什么关键词。返回空串表示不搜（如 ETF）"""
    if symbol in TICKER_ZH_KEYWORDS:
        return TICKER_ZH_KEYWORDS[symbol]
    if symbol.endswith(".HK") and name:
        # 港股的 name 一般已经是中文（如 "腾讯控股"），取前 2-3 字
        return name[:4].replace("控股", "").replace("集团", "").strip() or name
    if symbol.endswith(".US"):
        return symbol[:-3]  # 兜底 ticker
    return name or symbol


def search_for_position(
    session: _JinSession,
    symbol: str,
    name: str | None,
    flash_limit: int = 5,
    news_limit: int = 3,
) -> dict[str, list[dict]]:
    """对单只持仓搜金十。返回 {'flash': [...], 'news': [...]}。
    需要外部传入 session，由调用方管理 with 块。
    """
    kw = get_search_keyword(symbol, name)
    if not kw:
        return {"flash": [], "news": []}
    try:
        flash = session.search_flash(kw, limit=flash_limit)
        news = session.search_news(kw, limit=news_limit)
        return {"flash": flash, "news": news}
    except Exception as exc:
        logger.warning("Jin10 search for %s (%s) failed: %s", symbol, kw, exc)
        return {"flash": [], "news": []}
