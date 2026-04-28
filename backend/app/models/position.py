from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

class Position(Base):
    __tablename__ = "position"
    __table_args__ = (UniqueConstraint("symbol", "market", name="uq_position_symbol_market"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    market: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(100), default="")
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    available_qty: Mapped[int] = mapped_column(Integer, default=0)
    cost_price: Mapped[float] = mapped_column(Float, default=0.0)
    current_price: Mapped[float] = mapped_column(Float, default=0.0)
    prev_close: Mapped[float] = mapped_column(Float, default=0.0)
    market_value: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    day_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    day_pnl_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(10), default="HKD")
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
