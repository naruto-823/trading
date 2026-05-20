"""AI 决策建议持久化模型

每次 build_suggestions 生成的一批共享同一个 batch_id + generated_at；
单条 suggestion 可被驳回（dismissed_at）或采纳（adopted_decision_id 关联到 Decision.id）。
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Suggestion(Base):
    __tablename__ = "suggestion"

    # AI 给的 id（symbol-action 形式），同一标的同动作如果跨批次重复，靠 batch_id 区分
    # 这里用复合 PK 太麻烦，单独 row_id 做主键
    row_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    batch_id: Mapped[str] = mapped_column(String(64), index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")

    # AI 自己产的 stable id（symbol-action），方便前端跨批次去重 / 关联
    suggestion_key: Mapped[str] = mapped_column(String(120), index=True)

    action: Mapped[str] = mapped_column(String(20))   # buy / sell / add / stop_loss
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    qty: Mapped[str] = mapped_column(String(40), default="")
    price: Mapped[str] = mapped_column(String(200), default="")
    urgency: Mapped[str] = mapped_column(String(20), default="medium")
    thesis: Mapped[str] = mapped_column(Text, default="")

    # 复杂结构存 JSON
    data_points_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    affordability_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 辩论复核结果(Phase 2):{direction, winning_side, confidence, consistency,
    # bull_case, bear_case, judge_reasoning}
    debate_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 用户后续动作
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    adopted_decision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
