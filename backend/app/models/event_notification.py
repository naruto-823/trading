"""市场事件通知记录

event-watcher worker 每 30 min 跑：抓重仓股近期新闻 → LLM 识别重大事件 →
对每条事件用 event_hash 去重（同一事件不再重复推），命中且未推过 → 落库 + Bark 推送。
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class EventNotification(Base):
    __tablename__ = "event_notification"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # sha256(symbol + source_title)[:32] —— 同一事件不同时间段反复出现也不重复推
    event_hash: Mapped[str] = mapped_column(String(64), index=True, unique=True)
    notified_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )

    # 可能是宏观事件（如 FOMC、CPI），那就 symbol=None
    symbol: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    importance: Mapped[str] = mapped_column(String(20))  # high / medium
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)

    # 原新闻信息，用于 dedup + 用户回看
    source_title: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    push_status: Mapped[str] = mapped_column(String(20), default="sent")  # sent / failed
    push_error: Mapped[str | None] = mapped_column(Text, nullable=True)


# 复合索引：常按 symbol + 时间倒序查
Index("ix_event_symbol_time", EventNotification.symbol, EventNotification.notified_at.desc())
