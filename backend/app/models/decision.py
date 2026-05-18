"""交易决策日志模型

落库形态对应 frontend 的 Decision interface：每条决策一条记录，包括
冷静期、紧急原因、补仓检查清单（JSON 存）等。
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Decision(Base):
    __tablename__ = "decision"

    # UUID 字符串主键，由前端生成（或 service 兜底）
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    # 创建时间（毫秒 epoch 存成 DateTime 更通用）
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True
    )  # pending / executed / abandoned
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 决策内容
    action: Mapped[str] = mapped_column(String(20))  # buy / sell / add / stop_loss
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    qty: Mapped[str] = mapped_column(String(40), default="")
    price: Mapped[str] = mapped_column(String(100), default="")
    thesis: Mapped[str] = mapped_column(Text, default="")

    # 冷静期机制
    cooldown_hours: Mapped[int] = mapped_column(Integer, default=24)
    urgent_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 补仓 5 问检查清单（JSON 字符串；只在 action=add 时填）
    checklist_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 来源：手动 / AI 建议；如果是 AI 建议，记下原始 suggestion id 方便复盘
    source: Mapped[str] = mapped_column(String(40), default="manual")
    source_suggestion_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
