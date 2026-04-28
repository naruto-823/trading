from pydantic import BaseModel


class PnlSummaryItem(BaseModel):
    group: str
    total_pnl: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    market_value: float = 0.0
    cost_value: float = 0.0
