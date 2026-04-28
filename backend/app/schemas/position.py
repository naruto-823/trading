from datetime import datetime

from pydantic import BaseModel

class PositionResponse(BaseModel):
    id: int
    synced_at: datetime
    symbol: str
    market: str
    name: str
    quantity: int
    available_qty: int
    cost_price: float
    current_price: float
    prev_close: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_ratio: float
    day_pnl: float
    day_pnl_ratio: float
    currency: str

    model_config = {"from_attributes": True}
