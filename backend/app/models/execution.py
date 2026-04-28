from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Execution(Base):
    __tablename__ = "execution"

    execution_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    market: Mapped[str] = mapped_column(String(10))
    side: Mapped[str] = mapped_column(String(10))
    price: Mapped[float] = mapped_column(Float, default=0.0)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    trade_done_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    currency: Mapped[str] = mapped_column(String(10), default="HKD")
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    platform_fee: Mapped[float] = mapped_column(Float, default=0.0)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
