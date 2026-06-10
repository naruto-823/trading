"""每小时仓位体检报告持久化模型

每整点 generate_hourly_analysis 落一行;degraded=True 表示该轮某步降级。
spec: docs/superpowers/specs/2026-06-10-hourly-position-analysis-design.md
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PositionAnalysisReport(Base):
    __tablename__ = "position_analysis_report"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    # 账户快照(净资产/市值/现金/日盈亏 子集)
    account_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 本轮被分析的重仓清单
    positions_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # web_search 调研简报文本(降级时为空)
    research_brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AI 结构化输出:{overall_stance, per_position[], alerts[], summary}
    analysis_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    summary: Mapped[str] = mapped_column(Text, default="")
    push_status: Mapped[str] = mapped_column(String(20), default="pending")  # sent/failed/skipped/pending
    push_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
