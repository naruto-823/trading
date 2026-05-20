"""每日资产基线（用于复算"当日盈亏"对齐 Longbridge APP 口径）

长桥 OpenAPI 不返回 day_pnl 字段，APP 显示的"当日盈亏"是它客户端基于内部
基线快照算的。我们要对齐，就得自己存基线 — 每日北京时间 06:00 抓一次净资产
快照（这时美股 regular 已收盘、HK 还没开盘，是天然的"日切点"）。

day_pnl = current_net_assets − today_baseline.net_assets
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DailyBaseline(Base):
    __tablename__ = "daily_baseline"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 基线归属日：YYYY-MM-DD，"北京时间 06:00 boundary" 之后到下个 06:00 之前的所有时刻
    # 共享同一个 baseline_key（即取当日的 06:00 那一刻作为 day 起点）。
    baseline_key: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # 所有金额单位 HKD（账户主币）
    net_assets_hkd: Mapped[float] = mapped_column(Float, default=0.0)
    market_value_hkd: Mapped[float] = mapped_column(Float, default=0.0)
    total_cash_hkd: Mapped[float] = mapped_column(Float, default=0.0)
    total_pnl_hkd: Mapped[float] = mapped_column(Float, default=0.0)
    # capture 时刻的 FX 快照，用于事后审计 / 跨币种重算
    fx_rates_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 数据来源：snapshot（从最新 AccountSnapshot 抓的） / backfill（启动时从历史里挑的最近快照）
    source: Mapped[str] = mapped_column(String(20), default="snapshot")
