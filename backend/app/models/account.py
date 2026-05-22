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
    realized_day_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    # 今日卖出对当日盈亏的贡献，按市场拆分的原币金额，JSON: {"HK": 0.0, "US": 0.0, ...}
    # 前端在港股 / 美股卡片需要把它加到 Position.day_pnl 之上以反映已卖出标的的日内贡献。
    realized_day_pnl_by_market: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 融资 / 保证金信息（全部 HKD 口径）
    max_finance_amount: Mapped[float] = mapped_column(Float, default=0.0)
    remaining_finance_amount: Mapped[float] = mapped_column(Float, default=0.0)
    # 实际融资欠款（HKD）：把所有币种 cash_infos.available < 0 的部分折成 HKD 求和
    # 这是长桥 app "融资欠款" 那一栏的口径，与 max-remaining 不同。
    outstanding_debt: Mapped[float] = mapped_column(Float, default=0.0)
    # 未配发的 IPO 申购占款（HKD）。account_balance 不含这笔（申购款已离开 total_cash、
    # 新股未上市又不在 positions），前端把它加回净资产/现金以对齐长桥 app。
    pending_ipo: Mapped[float] = mapped_column(Float, default=0.0)
    init_margin: Mapped[float] = mapped_column(Float, default=0.0)
    maintenance_margin: Mapped[float] = mapped_column(Float, default=0.0)
    buy_power: Mapped[float] = mapped_column(Float, default=0.0)
    margin_call: Mapped[int] = mapped_column(Integer, default=0)
    risk_level: Mapped[int] = mapped_column(Integer, default=0)
    # 按币种拆分的现金明细，JSON: [{"currency":"HKD","available":...,"withdraw":...,"frozen":...,"settling":...}, ...]
    cash_infos_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 同步时刻的 FX 汇率快照，JSON: {"USD_HKD": 7.83, "HKD_CNY": 0.92, ...}
    # 前端用来切换 HKD/CNY/USD 显示
    fx_rates_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
