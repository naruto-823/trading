"""价格告警规则

支持的条件：
- price_above / price_below：现价突破阈值
- day_change_pct_above / day_change_pct_below：当日涨跌幅突破阈值（百分数，正数 = 涨）

冷却期：触发一次后，cooldown_minutes 内不再重复触发同一规则，避免刷屏。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Alert(Base):
    __tablename__ = "alert"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    symbol: Mapped[str] = mapped_column(String(40), index=True)
    # 见上方注释；schema 层有 Enum 校验
    condition: Mapped[str] = mapped_column(String(40))
    threshold: Mapped[float] = mapped_column(Float)

    note: Mapped[str] = mapped_column(Text, default="")
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=60)

    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 命中次数（生命周期内）
    trigger_count: Mapped[int] = mapped_column(Integer, default=0)
