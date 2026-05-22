import json
from datetime import datetime

from pydantic import AliasChoices, BaseModel, Field, field_validator


class CashInfoBreakdown(BaseModel):
    currency: str
    available: float
    withdraw: float
    frozen: float
    settling: float


class AccountSnapshotResponse(BaseModel):
    id: int
    synced_at: datetime
    currency: str
    total_cash: float
    net_assets: float
    market_value: float
    total_pnl: float
    day_pnl: float
    realized_day_pnl: float = 0.0
    # 已卖出标的对当日盈亏的贡献，按市场拆分（原币）。前端在 HK/US 卡片需要叠加此项。
    realized_day_pnl_by_market: dict[str, float] = {}
    # 融资 / 保证金
    max_finance_amount: float = 0.0
    remaining_finance_amount: float = 0.0
    # 未配发的 IPO 申购占款（HKD）。account_balance 不含这笔，前端加回净资产/现金。
    pending_ipo: float = 0.0
    # 实际融资欠款（HKD）：所有币种 cash_infos.available 负数部分的合计，
    # 与长桥 app "融资欠款" 字段口径一致。负数表示借款。
    outstanding_debt: float = 0.0
    init_margin: float = 0.0
    maintenance_margin: float = 0.0
    buy_power: float = 0.0
    margin_call: int = 0
    risk_level: int = 0
    # 按币种拆分的现金明细（USD 负数 = 美元账户透支借款）
    # ORM 列名是 cash_infos_json（存的 JSON 字符串），API 字段叫 cash_infos
    cash_infos: list[CashInfoBreakdown] = Field(
        default_factory=list,
        validation_alias=AliasChoices("cash_infos", "cash_infos_json"),
    )
    # 同步时刻的 FX 汇率快照
    fx_rates: dict[str, float] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("fx_rates", "fx_rates_json"),
    )

    @field_validator("fx_rates", mode="before")
    @classmethod
    def _parse_fx_rates(cls, v):
        if v is None or v == "":
            return {}
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return {}
        return v

    model_config = {"from_attributes": True}

    @field_validator("realized_day_pnl_by_market", mode="before")
    @classmethod
    def _parse_market_json(cls, v):
        if v is None or v == "":
            return {}
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return {}
        return v

    @field_validator("cash_infos", mode="before")
    @classmethod
    def _parse_cash_infos_json(cls, v):
        # SQLAlchemy attribute is `cash_infos_json`; pydantic field aliased to `cash_infos`.
        # 从 ORM 读出来时拿的就是 list[CashInfoBreakdown] 形式或 JSON 字符串。
        if v is None or v == "":
            return []
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return []
        return v
