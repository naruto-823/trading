"""决策日志的 pydantic schema

注意 created_at / executed_at 在 wire 上用毫秒 epoch（int），跟前端一致；
ORM 层是 datetime。在 from-orm 时做转换。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator


Action = Literal["buy", "sell", "add", "stop_loss"]
Status = Literal["pending", "executed", "abandoned"]


class ChecklistPayload(BaseModel):
    """补仓 5 问检查清单（前端字段对齐）"""

    currentLossPct: str = ""
    isLeveraged: bool = False
    thesisChanged: str = ""
    willExceedConcentration: bool = False
    catalyst: str = ""
    exitPlan: str = ""


class DecisionCreate(BaseModel):
    """新建决策请求体"""

    id: str | None = None  # 不传则后端生成 UUID
    action: Action
    symbol: str
    qty: str = ""
    price: str = ""
    thesis: str = ""
    cooldown_hours: int = Field(default=24, ge=0, le=168)
    urgent_reason: str | None = None
    checklist: ChecklistPayload | None = None
    source: str = "manual"
    source_suggestion_id: str | None = None
    # 兼容旧 localStorage 迁移时带的时间戳（毫秒 epoch）
    created_at_ms: int | None = None


class DecisionUpdate(BaseModel):
    """更新状态（执行 / 作废）"""

    status: Status


class DecisionResponse(BaseModel):
    id: str
    # ORM 是 created_at: datetime；前端需要毫秒 epoch
    created_at_ms: int = Field(
        validation_alias=AliasChoices("created_at_ms", "created_at")
    )
    status: Status
    executed_at_ms: int | None = Field(
        default=None,
        validation_alias=AliasChoices("executed_at_ms", "executed_at"),
    )
    action: Action
    symbol: str
    qty: str
    price: str
    thesis: str
    cooldown_hours: int
    urgent_reason: str | None
    # ORM 是 checklist_json: str；前端需要结构化对象
    checklist: ChecklistPayload | None = Field(
        default=None, validation_alias=AliasChoices("checklist", "checklist_json")
    )
    source: str
    source_suggestion_id: str | None

    model_config = {"from_attributes": True, "populate_by_name": True}

    @field_validator("created_at_ms", mode="before")
    @classmethod
    def _conv_created(cls, v):
        return _dt_to_ms(v)

    @field_validator("executed_at_ms", mode="before")
    @classmethod
    def _conv_executed(cls, v):
        return _dt_to_ms(v) if v else None

    @field_validator("checklist", mode="before")
    @classmethod
    def _parse_checklist(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return None
        return v


def _dt_to_ms(v) -> int:
    if v is None:
        return 0
    if isinstance(v, datetime):
        return int(v.timestamp() * 1000)
    if isinstance(v, (int, float)):
        return int(v)
    return 0
