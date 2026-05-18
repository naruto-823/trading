"""快通道：源自标 important 的宏观快讯，跳过 LLM 直接推 Bark

设计 rationale：
- event-watcher 30min 跑 + LLM 判断有明显延迟，错过 Jin10 时效性强的快讯
- 但全 source 直推会噪声爆炸（金十每天几百条）
- 折中：只直推"源标重要 + 关键词命中（macro/portfolio 相关）"的，约每天 5-15 条

去重靠现有的 event_notification 表（event_hash 一致就不再推），跟 event-watcher 共用。
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.event_notification import EventNotification
from app.services.macro_feed import MacroFlash, fetch_macro_news
from app.services.notify import send_bark

logger = logging.getLogger(__name__)

# 关键词白名单：源标 important 但不在这些 topic 里的（如"巴西央行调息"、"国内某省汽车产销"）跳过
# 用全小写 substring 匹配（content + title 一起匹配）
PUSH_KEYWORDS = {
    # 货币政策
    "美联储", "fed", "fomc", "加息", "降息", "美债", "国债", "yield", "收益率",
    "cpi", "ppi", "通胀", "通膨", "非农", "失业", "就业",
    # 大宗 / 油
    "油价", "原油", "wti", "布伦特",
    # 中美 / 关税
    "中美", "关税", "制裁", "出口管制",
    # 地缘
    "伊朗", "以色列", "俄乌", "乌克兰", "台海", "中东", "战争", "冲突", "war",
    # 央行 / 央妈
    "央行", "降准", "ecb", "boe", "boj",
    # 科技/AI/半导体
    "英伟达", "nvda", "微软", "msft", "苹果", "aapl", "特斯拉", "tsla",
    "谷歌", "alphabet", "meta", "ai", "人工智能", "半导体", "芯片",
    "台积电", "tsm", "英特尔", "intc",
    # 港股核心
    "腾讯", "阿里", "美团",
    # 其他宏观关键
    "白宫", "trump", "特朗普", "鲍威尔",
}

MIN_IMPORTANCE_FOR_PUSH = 4  # 源标"重要"的（Jin10 important=1、cailianshe is_red、wallstcn score>=2）


def _key_text(item: MacroFlash) -> str:
    return f"{item.title}\n{item.content}".lower()


def matches_keywords(item: MacroFlash) -> bool:
    text = _key_text(item)
    return any(kw in text for kw in PUSH_KEYWORDS)


def _hash_macro_event(item: MacroFlash) -> str:
    """跟 event_watcher._hash_event 同算法（避免重复推），key 用 source+content 前 N 字"""
    # macro 事件没有 symbol，用 source + content 头部做 key
    key = f"macro|{item.source}|{(item.content or item.title)[:120]}".lower()
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def _format_push(item: MacroFlash) -> tuple[str, str]:
    """构造 (title, body)"""
    source_label = {"jin10": "金十", "cailianshe": "财联社", "wallstcn": "华尔街见闻"}.get(item.source, item.source)
    title = f"📰 [{source_label}] {item.title[:30]}{'…' if len(item.title) > 30 else ''}"
    body = item.content if item.content and item.content != item.title else item.title
    if len(body) > 400:
        body = body[:400] + "…"
    return title, body


def run_macro_flash(db: Session) -> dict[str, int]:
    """跑一次：拉 macro_feed → 过滤 → 去重 → 推 → 入库"""
    stats = {"fetched": 0, "filtered": 0, "fired": 0, "deduped": 0, "failed": 0}

    items = fetch_macro_news(min_importance=MIN_IMPORTANCE_FOR_PUSH, hours_back=2, limit_per_source=20)
    stats["fetched"] = len(items)

    candidates = [it for it in items if matches_keywords(it)]
    stats["filtered"] = len(candidates)

    for item in candidates:
        h = _hash_macro_event(item)
        existing = db.query(EventNotification).filter_by(event_hash=h).first()
        if existing:
            stats["deduped"] += 1
            continue

        title, body = _format_push(item)
        # macro 直推用 active level（非 timeSensitive，避免半夜睡觉响）
        # 真正紧急的事件 event-watcher LLM 会用 timeSensitive 复推（hash 一致会去重，所以不会）
        # 改成跟 importance 挂钩
        level = "timeSensitive" if item.importance >= 5 else "active"
        result = send_bark(title, body, level=level, group="market-events", sound="chime")

        rec = EventNotification(
            id=uuid.uuid4().hex,
            event_hash=h,
            notified_at=datetime.utcnow(),
            symbol=None,  # macro
            importance="high" if item.importance >= 5 else "medium",
            title=item.title[:200],
            body=body,
            source_title=f"[{item.source}] {item.title}"[:500],
            push_status="sent" if result["ok"] else "failed",
            push_error=None if result["ok"] else str(result["detail"])[:500],
        )
        db.add(rec)
        db.commit()

        if result["ok"]:
            stats["fired"] += 1
            logger.info("macro-flash fired [%s imp=%d]: %s", item.source, item.importance, item.title[:80])
        else:
            stats["failed"] += 1

    return stats
