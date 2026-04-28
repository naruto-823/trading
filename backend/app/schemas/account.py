from datetime import datetime

from pydantic import BaseModel


class AccountSnapshotResponse(BaseModel):
    id: int
    synced_at: datetime
    currency: str
    total_cash: float
    net_assets: float
    market_value: float
    total_pnl: float
    day_pnl: float

    model_config = {"from_attributes": True}
