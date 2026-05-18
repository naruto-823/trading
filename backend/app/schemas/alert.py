"""告警规则的 pydantic schema"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator


AlertCondition = Literal[
    "price_above",
    "price_below",
    "day_change_pct_above",
    "day_change_pct_below",
]


class AlertCreate(BaseModel):
    symbol: str
    condition: AlertCondition
    threshold: float
    note: str = ""
    cooldown_minutes: int = Field(default=60, ge=1, le=10080)
    enabled: bool = True


class AlertUpdate(BaseModel):
    """部分字段更新；全部 optional"""

    enabled: bool | None = None
    threshold: float | None = None
    note: str | None = None
    cooldown_minutes: int | None = Field(default=None, ge=1, le=10080)
    condition: AlertCondition | None = None
    # 允许重置 last_triggered_at（前端"重置冷却"按钮用）
    reset_cooldown: bool = False


class AlertResponse(BaseModel):
    id: str
    created_at_ms: int = Field(
        validation_alias=AliasChoices("created_at_ms", "created_at")
    )
    enabled: bool
    symbol: str
    condition: AlertCondition
    threshold: float
    note: str
    cooldown_minutes: int
    last_triggered_at_ms: int | None = Field(
        default=None,
        validation_alias=AliasChoices("last_triggered_at_ms", "last_triggered_at"),
    )
    trigger_count: int

    model_config = {"from_attributes": True, "populate_by_name": True}

    @field_validator("created_at_ms", mode="before")
    @classmethod
    def _conv_created(cls, v):
        return _dt_to_ms(v)

    @field_validator("last_triggered_at_ms", mode="before")
    @classmethod
    def _conv_last(cls, v):
        return _dt_to_ms(v) if v else None


def _dt_to_ms(v) -> int:
    if v is None:
        return 0
    if isinstance(v, datetime):
        return int(v.timestamp() * 1000)
    if isinstance(v, (int, float)):
        return int(v)
    return 0
