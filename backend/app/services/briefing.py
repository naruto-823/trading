"""每日 AI 复盘服务

进 Dashboard 时调用：选重仓 → 抓最近新闻 + 大盘背景 → 喂 LLM → 返回结构化 JSON。
带 TTL 缓存避免重复烧钱。
"""

from __future__ import annotations

import json
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from anthropic import Anthropic
from openai import OpenAI
from sqlalchemy.orm import Session

from app.config import settings
from app.services.positions import list_positions

logger = logging.getLogger(__name__)

# 缓存：key = 本地日期 + 重仓 symbol 列表 hash；value = (briefing, generated_at_ts)
_BRIEFING_CACHE: dict[str, tuple[dict, float]] = {}

CACHE_TTL_SECONDS = 20 * 60  # 20 min，足够避免重复烧钱又能反映新行情

HEAVY_TOP_N = 5
HEAVY_MIN_RATIO = 0.03  # 至少占总市值 3% 才算重仓

NEWS_PER_STOCK = 4

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
STOOQ_QUOTE_URL = "https://stooq.com/q/l/"

# 新闻只看最近 N 小时的，避免塞太老的内容给 LLM
NEWS_MAX_AGE_HOURS = 48

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# 大盘背景指标。key = 对外 symbol（前端展示用），value = (Stooq 查询 symbol, 中文短名)
# 现货指数（^SPX/^NDQ）夜盘不更新，必须同时塞期货（ES.F/NQ.F）才能反映 overnight 行情。
MARKET_CONTEXT = {
    "^GSPC": ("^spx", "标普500"),
    "ES=F": ("es.f", "标普期货"),
    "^IXIC": ("^ndq", "纳指综合"),
    "NQ=F": ("nq.f", "纳指期货"),
    "CL=F": ("cl.f", "原油"),
    "^HSI": ("^hsi", "恒指"),
}


def _to_yahoo_symbol(symbol: str) -> str:
    """长桥 symbol → Yahoo symbol。

    AAPL.US → AAPL；700.HK → 0700.HK（Yahoo 港股要 4 位补零）。
    """
    if symbol.endswith(".US"):
        return symbol[:-3]
    if symbol.endswith(".HK"):
        code = symbol[:-3]
        try:
            return f"{int(code):04d}.HK"
        except ValueError:
            return symbol
    return symbol


def select_heavy_positions(db: Session) -> list[dict]:
    """Top N + ≥3% 双门槛，排除期权（symbol > 8 字符的视为期权）。"""
    positions = list_positions(db)
    stocks = [p for p in positions if len(p.symbol) <= 8 and p.market_value != 0]
    if not stocks:
        return []
    total_mv = sum(abs(p.market_value) for p in stocks)
    if total_mv <= 0:
        return []
    stocks.sort(key=lambda p: abs(p.market_value), reverse=True)

    heavy = []
    for p in stocks[:HEAVY_TOP_N]:
        ratio = abs(p.market_value) / total_mv
        if ratio < HEAVY_MIN_RATIO:
            continue
        heavy.append({
            "symbol": p.symbol,
            "name": p.name,
            "market": p.market,
            "quantity": p.quantity,
            "cost_price": p.cost_price,
            "current_price": p.current_price,
            "market_value": p.market_value,
            "ratio": ratio,
            "unrealized_pnl": p.unrealized_pnl,
            "unrealized_pnl_ratio": p.unrealized_pnl_ratio,
            "day_pnl": p.day_pnl,
            "day_pnl_ratio": p.day_pnl_ratio,
            "currency": p.currency,
        })
    return heavy


def fetch_news_for_symbol(
    symbol: str,
    client: httpx.Client | None = None,  # 兼容旧 signature，新实现不再依赖外部 client
    name: str | None = None,
    limit: int = NEWS_PER_STOCK,
) -> list[dict]:
    """走 news_sources 的 fallback 链：Finnhub → Tavily → Brave → Google News RSS。
    返回结构跟之前一致（dict 列表），保持调用方零改动。
    """
    from app.services.news_sources import fetch_news

    items = fetch_news(symbol, name=name, limit=limit)
    return [
        {
            "title": it.title,
            "publisher": it.publisher,
            "link": it.url,
            "published_at": it.published_at.isoformat() if it.published_at else None,
            "summary": it.summary,
            "source_tier": it.source_tier,
        }
        for it in items
    ]


def _fetch_stooq_one(client: httpx.Client, stooq_sym: str) -> tuple[float | None, float | None]:
    """单只 Stooq 拉 OHLC，返回 (current_price, open_price)。
    Stooq 对未开盘/无数据的标的会返回 'N/D'，此时返回 (None, None)。
    """
    try:
        resp = client.get(
            STOOQ_QUOTE_URL,
            params={"s": stooq_sym, "f": "sd2t2ohlc", "h": "", "e": "csv"},
        )
        resp.raise_for_status()
        lines = resp.text.strip().splitlines()
        if len(lines) < 2:
            return None, None
        cols = lines[1].split(",")
        # Symbol,Date,Time,Open,High,Low,Close
        if len(cols) < 7 or cols[3] == "N/D":
            return None, None
        try:
            open_p = float(cols[3])
            close_p = float(cols[6])
            return close_p, open_p
        except ValueError:
            return None, None
    except Exception as exc:
        logger.warning("Stooq fetch failed for %s: %s", stooq_sym, exc)
        return None, None


def fetch_market_context(client: httpx.Client) -> dict:
    """抓宏观指标。change_percent 用今日 open→close 估算（盘中即为当日涨跌）。"""
    ctx: dict[str, dict] = {}
    for display_sym, (stooq_sym, label) in MARKET_CONTEXT.items():
        price, open_p = _fetch_stooq_one(client, stooq_sym)
        if price is None or open_p is None or open_p == 0:
            continue
        pct = (price - open_p) / open_p * 100
        ctx[display_sym] = {
            "name": label,
            "price": price,
            "change_percent": pct,
        }
    return ctx


SYSTEM_PROMPT = """你是用户的个人投资顾问。用户在每次打开仪表盘时让你做一次简洁的复盘，已签知情免责声明。

输入：今日大盘背景、用户重仓持仓、每只票最近的新闻标题。
输出：严格 JSON（不要 markdown 包裹、不要前后说明），按以下 schema：

{
  "market_summary": "2-3 句话总结今日大盘风向：涨跌主因、风险点、对组合的整体影响",
  "stocks": [
    {"symbol": "AAPL.US", "bullish": "...", "bearish": "...", "suggestion": "..."}
  ],
  "overall_action": "1-2 句话总体建议（仓位调整方向、需关注事件、风险提示）"
}

时段判断（重要）：
- 现货指数（标普500/纳指综合）只在常规交易时段更新，夜盘/周末显示的是上一交易日收盘数
- 期货（标普期货/纳指期货）24h 跑，反映 overnight 真实情绪
- 如果现货和期货明显分歧（如现货收平但期货跌 0.5%+），优先以期货为准描述"夜盘 / 盘前"的实时情绪，并明说"现货是 X 日收盘，期货反映夜盘 -X%"
- 美东时间 16:00-09:30 ET（北京次日 04:00-21:30）属于夜盘/盘前时段，此时市场叙事看期货

规则：
- 全部中文，每个字段一句话即可，不要废话
- stocks 数组的顺序必须和输入【重仓持仓】一致，symbol 严格复用输入值
- bullish/bearish/suggestion 各 30-60 字，结合新闻 + 持仓盈亏给出方向性观点
- 建议要具体（继续持有 / 关注 XX 价位 / 减仓警惕 / 等下周财报），不要"投资有风险"这种万金油
- 没新闻就基于基本面 + 持仓盈亏推断
- 严格只输出 JSON，不要包含 headlines 字段（服务端会自己填）"""


def build_briefing(db: Session, force_refresh: bool = False) -> dict:
    heavy = select_heavy_positions(db)
    if not heavy:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cache_hit": False,
            "market_summary": "暂无持仓数据，请先同步账户。",
            "stocks": [],
            "overall_action": "",
            "context": {},
        }

    cache_key = _cache_key(heavy)
    now_ts = time.time()

    if not force_refresh:
        cached = _BRIEFING_CACHE.get(cache_key)
        if cached:
            briefing, ts = cached
            if now_ts - ts < CACHE_TTL_SECONDS:
                return {
                    **briefing,
                    "cache_hit": True,
                    "generated_at": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                }

    # 抓新闻 + 大盘背景（follow_redirects 处理 Google News 的 302）
    with httpx.Client(timeout=10.0, headers=HTTP_HEADERS, follow_redirects=True) as client:
        market_ctx = fetch_market_context(client)
        for stock in heavy:
            stock["news"] = fetch_news_for_symbol(stock["symbol"], client, name=stock["name"])

    # 调 LLM（只产生分析；新闻 headlines 由下面服务端直接合并）
    # 优先 Anthropic 原生协议（claude-opus-4-7，质量更好且无 idealab 风控）
    if settings.anthropic_api_key:
        briefing = _call_llm_anthropic(heavy, market_ctx)
    elif settings.validate_ai():
        briefing = _call_llm_openai(heavy, market_ctx)
    else:
        briefing = _mock_briefing(heavy, reason="AI 未配置，仅展示基础数据")

    # 服务端把抓到的新闻合并进 stocks[*].headlines（避免让 LLM 回显浪费 token）
    news_by_symbol = {p["symbol"]: p.get("news", []) for p in heavy}
    for s in briefing.get("stocks", []):
        sym = s.get("symbol")
        news = news_by_symbol.get(sym, [])
        s["headlines"] = [{"title": n["title"], "url": n["link"]} for n in news[:3]]

    briefing["context"] = market_ctx
    _BRIEFING_CACHE[cache_key] = (briefing, now_ts)
    return {
        **briefing,
        "cache_hit": False,
        "generated_at": datetime.fromtimestamp(now_ts, timezone.utc).isoformat(),
    }


def _cache_key(heavy: list[dict]) -> str:
    symbols = "_".join(sorted(p["symbol"] for p in heavy))
    return f"{datetime.now().strftime('%Y%m%d')}::{symbols}"


def _build_user_payload(heavy: list[dict], market_ctx: dict) -> str:
    payload = {
        "现在时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "市场背景": market_ctx,
        "重仓持仓": [
            {
                "symbol": p["symbol"],
                "名称": p["name"],
                "市值占比": f"{p['ratio'] * 100:.1f}%",
                "浮动盈亏率": f"{p['unrealized_pnl_ratio'] * 100:.1f}%",
                "当日涨跌": f"{p['day_pnl_ratio'] * 100:.2f}%",
                "成本价": p["cost_price"],
                "现价": p["current_price"],
                "货币": p["currency"],
                "近期新闻标题": [n["title"] for n in p.get("news", [])[:4]],
            }
            for p in heavy
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _call_llm_anthropic(heavy: list[dict], market_ctx: dict) -> dict:
    """走 Anthropic 原生协议（/v1/messages）。"""
    client = Anthropic(
        api_key=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
    )
    user_content = _build_user_payload(heavy, market_ctx)

    try:
        resp = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        # Anthropic 返回 content 是 block list；取第一个 text block
        text_parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        text = "".join(text_parts) or "{}"
        return _parse_json(text)
    except Exception as exc:
        logger.error("Anthropic briefing failed: %s", exc, exc_info=True)
        return _mock_briefing(heavy, reason=f"AI 调用失败：{exc}")


def _call_llm_openai(heavy: list[dict], market_ctx: dict) -> dict:
    """走 OpenAI 兼容协议（fallback，给 ideaLAB qwen 等用）。"""
    api_key = settings.ai_api_key
    base_url = settings.ai_base_url or None
    client = OpenAI(api_key=api_key, base_url=base_url)
    user_content = _build_user_payload(heavy, market_ctx)

    try:
        response = client.chat.completions.create(
            model=settings.ai_model,
            max_tokens=1500,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
        text = response.choices[0].message.content or "{}"
        return _parse_json(text)
    except Exception as exc:
        logger.error("OpenAI briefing failed: %s", exc, exc_info=True)
        return _mock_briefing(heavy, reason=f"AI 调用失败：{exc}")


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    # 去掉 ```json ... ``` 包裹
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline > 0:
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("LLM 输出非合法 JSON: %s | raw: %s", exc, text[:200])
        return {
            "market_summary": "AI 输出解析失败，请刷新重试。",
            "stocks": [],
            "overall_action": "",
            "_raw": text[:500],
        }


def _mock_briefing(heavy: list[dict], reason: str) -> dict:
    return {
        "market_summary": reason,
        "stocks": [
            {
                "symbol": p["symbol"],
                "headlines": [
                    {"title": n["title"], "url": n["link"]}
                    for n in p.get("news", [])[:3]
                ],
                "bullish": "—",
                "bearish": "—",
                "suggestion": "—",
            }
            for p in heavy
        ],
        "overall_action": "",
    }
