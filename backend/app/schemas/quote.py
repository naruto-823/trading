from pydantic import BaseModel

class QuoteResponse(BaseModel):
    symbol: str
    name: str = ""
    # 当前价（按时段优先级选取：盘前 / 盘中 / 盘后 / 已收盘）
    current_price: float = 0.0
    # 上一个常规交易日收盘价（当日盈亏的恒定基准）
    prev_close: float = 0.0
    # 常规盘数据
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    last_done: float = 0.0
    volume: int = 0
    turnover: float = 0.0
    # 盘前数据（仅美股；非交易时段或无数据时为 0）
    pre_market_price: float = 0.0
    pre_market_change: float = 0.0
    pre_market_change_ratio: float = 0.0
    # 盘后数据（仅美股；非交易时段或无数据时为 0）
    post_market_price: float = 0.0
    post_market_change: float = 0.0
    post_market_change_ratio: float = 0.0
    # 当前所处的交易时段：pre / regular / post / closed
    trading_session: str = "closed"
    # 基于 current_price 与 prev_close 的涨跌
    change: float = 0.0
    change_ratio: float = 0.0
    timestamp: str = ""