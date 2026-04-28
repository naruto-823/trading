from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AccountSnapshot(Base):
    __tablename__ = "account_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    currency: Mapped[str] = mapped_column(String(10), default="HKD")
    total_cash: Mapped[float] = mapped_column(Float, default=0.0)
    net_assets: Mapped[float] = mapped_column(Float, default=0.0)
    market_value: Mapped[float] = mapped_column(Float, default=0.0)
    total_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    day_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
