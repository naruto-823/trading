"""市场事件监控

每 30 min 跑：抓重仓 Top 5 的最近新闻 → 喂 Opus 严格筛选"用户必须知道"的事件 →
对每条事件用 sha256(symbol + source_title) 去重 → 命中且未推过 → Bark 推送 + 落库。

为啥用 LLM 不用关键词：关键词太机械会漏会误，让 Opus 语义判断"这条对用户的具体影响"。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

import httpx
from anthropic import Anthropic
from sqlalchemy.orm import Session

from app.config import settings
from app.models.event_notification import EventNotification
from app.services.news_sources import HTTP_HEADERS, fetch_news
from app.services.notify import send_bark
from app.services.positions import list_positions

logger = logging.getLogger(__name__)

# Top N 持仓喂给 LLM（更多新闻 ≠ 更准；噪声反而上升）
TOP_N_POSITIONS = 5
NEWS_PER_STOCK = 4


SYSTEM_PROMPT = """你是用户的市场事件监控员。基于他重仓股的近期新闻，**严格筛选**真正"用户必须立刻知道"的重大事件。

【明确排除】这些不要报：
- 分析师价格目标 / 评级调整（除非来自顶级机构 + 调整幅度 > 30%）
- 机构持仓小动作（除非单一机构变动 > 5% 流通股）
- 日常技术面分析 / 涨跌幅报道 / "盘前下跌 1%" 这种
- 营销推广 / 列表式文章（"3 只值得买的股票"）
- 重复新闻：同一事件多家报道，**只挑最早 / 最权威一条**

【保留】这些才报：
- **财报**：发布日临近 / 财报指引调整 / 业绩超预期或不及预期
- **产品大新闻**：新品发布 / 召回 / 重大缺陷 / 服务大宕机
- **M&A**：收购 / 合并 / 资产剥离（涉及金额 > $1B 或战略重大）
- **法律 action**：诉讼 / 监管调查 / 罚款 / 反垄断
- **高管变动**：CEO / CFO / 核心高管离任
- **突发利空 / 利好**：黑天鹅 / 监管政策变化
- **宏观**：FOMC 决议、CPI 等关键数据发布、地缘升级（仅当对持仓有显著影响）

【输出格式】严格 JSON（不要 markdown 包裹）：

{
  "events": [
    {
      "symbol": "MSFT.US" | null,
      "importance": "high" | "medium",
      "title": "20 字内推送标题",
      "body": "1-2 句话：事件 + 对用户的影响。必须明确说'对持仓的影响'",
      "source_title": "原新闻完整标题（用于去重）"
    }
  ]
}

【硬规则】
- events 数组**经常为空**——多数时间段没有重大事件，这是正常的。**没事件就返回 {"events": []}，不要硬凑**
- 只输出 high / medium；low 不要
- title 简短到锁屏一眼能判断"要不要进一步看"
- body 必须包含"对用户的影响"（如"持仓亏损可能扩大"、"利好兑现，可考虑止盈"、"短期不确定性升温"）
- 同一事件多家报道时，只输出一次（按你判断最权威的那条）
- 不要凑数；宁可空也不要给低质量事件

【输入字段说明】
- news 每条带 title / publisher / summary / source_tier
- summary 是新闻正文摘要（Finnhub / Tavily 提供，比 title 信息更全）。**有 summary 时优先基于 summary 判断**，避免单凭标题误判
- source_tier=finnhub 是专业财经源；google_rss 是兜底，质量参差，要更严格"""


def _hash_event(symbol: str | None, source_title: str, title: str) -> str:
    key = f"{symbol or 'macro'}|{source_title or title}".lower()
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def detect_events(db: Session) -> list[dict]:
    """跑一次 detection：抓新闻 + 喂 Opus + 返回结构化事件列表（未去重）"""
    if not settings.anthropic_api_key:
        logger.info("event-watcher: no anthropic key, skip")
        return []

    positions = list_positions(db)
    stocks = sorted(
        [p for p in positions if len(p.symbol) <= 8 and abs(p.market_value) > 0],
        key=lambda p: abs(p.market_value),
        reverse=True,
    )[:TOP_N_POSITIONS]
    if not stocks:
        return []

    # 抓新闻：用 news_sources 的 fallback 链（Finnhub → Tavily → Brave → Google News）
    news_by_symbol: dict[str, list[dict]] = {}
    with httpx.Client(timeout=10.0, headers=HTTP_HEADERS, follow_redirects=True) as client:
        for p in stocks:
            items = fetch_news(p.symbol, name=p.name, limit=NEWS_PER_STOCK, client=client)
            news_by_symbol[p.symbol] = [it.to_dict() for it in items]

    # 没新闻直接返回，省一次 LLM 调用
    if all(not v for v in news_by_symbol.values()):
        return []

    payload = {
        "现在时间": datetime.now(timezone.utc).isoformat(),
        "持仓Top5": [
            {
                "symbol": p.symbol,
                "名称": p.name,
                "市值": p.market_value,
                "浮盈率": f"{p.unrealized_pnl_ratio * 100:.1f}%",
            }
            for p in stocks
        ],
        "近期新闻": {
            # title + publisher + summary（Finnhub/Tavily 有 summary，RSS 没有）
            # 给 LLM 提供 summary 时判断会更准确，避免单凭标题误判
            sym: [
                {
                    "title": n["title"],
                    "publisher": n.get("publisher", ""),
                    "summary": (n.get("summary") or "")[:400],  # 截断防 token 爆炸
                    "source_tier": n.get("source_tier"),
                }
                for n in news
            ]
            for sym, news in news_by_symbol.items()
        },
    }

    client = Anthropic(
        api_key=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
    )
    try:
        resp = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}],
        )
        text_parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        text = "".join(text_parts).strip()
        if text.startswith("```"):
            first_nl = text.find("\n")
            if first_nl > 0:
                text = text[first_nl + 1:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        data = json.loads(text)
        return data.get("events", []) or []
    except Exception as exc:
        logger.warning("event-watcher LLM call failed: %s", exc)
        return []


def process_events(db: Session, events: list[dict]) -> dict[str, int]:
    """对每条事件去重 + 推 Bark + 入库。返回 {"detected": N, "fired": M, "deduped": D, "failed": F}"""
    stats = {"detected": len(events), "fired": 0, "deduped": 0, "failed": 0}
    for ev in events:
        symbol = ev.get("symbol")
        title = (ev.get("title") or "").strip()
        body = (ev.get("body") or "").strip()
        source_title = (ev.get("source_title") or "").strip()
        importance = ev.get("importance", "medium")

        if not title:
            continue

        h = _hash_event(symbol, source_title, title)
        existing = db.query(EventNotification).filter_by(event_hash=h).first()
        if existing:
            stats["deduped"] += 1
            continue

        # high 用 timeSensitive 突破专注模式；medium 普通推送
        level = "timeSensitive" if importance == "high" else "active"
        full_title = f"📰 {title}" if symbol is None else f"📰 {symbol} | {title}"
        result = send_bark(full_title, body, level=level, group="market-events", sound="chime")

        rec = EventNotification(
            id=uuid.uuid4().hex,
            event_hash=h,
            notified_at=datetime.utcnow(),
            symbol=symbol,
            importance=importance,
            title=title,
            body=body,
            source_title=source_title,
            push_status="sent" if result["ok"] else "failed",
            push_error=None if result["ok"] else str(result["detail"])[:500],
        )
        db.add(rec)
        db.commit()

        if result["ok"]:
            stats["fired"] += 1
            logger.info("event-watcher fired: %s [%s] %s", symbol or "MACRO", importance, title)
        else:
            stats["failed"] += 1
            logger.warning("event-watcher push failed for %s: %s", title, result["detail"])
    return stats


def list_recent_events(db: Session, days: int = 7) -> list[EventNotification]:
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)
    return (
        db.query(EventNotification)
        .filter(EventNotification.notified_at >= cutoff)
        .order_by(EventNotification.notified_at.desc())
        .all()
    )
