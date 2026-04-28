from datetime import datetime

from pydantic import BaseModel


class ExecutionResponse(BaseModel):
    execution_id: str
    order_id: str
    symbol: str
    market: str
    side: str
    price: float
    quantity: int
    trade_done_at: datetime
    currency: str
    commission: float
    platform_fee: float

    model_config = {"from_attributes": True}
