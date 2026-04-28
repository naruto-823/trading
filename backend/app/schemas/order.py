from datetime import datetime

from pydantic import BaseModel


class OrderResponse(BaseModel):
    order_id: str
    symbol: str
    market: str
    side: str
    order_type: str
    status: str
    submitted_qty: int
    filled_qty: int
    avg_price: float
    submitted_at: datetime | None
    updated_at: datetime | None

    model_config = {"from_attributes": True}
