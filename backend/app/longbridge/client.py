"""长桥 OpenAPI 客户端单例管理（带重试）"""

import time
from functools import lru_cache

from longport.openapi import Config, QuoteContext, TradeContext

from app.config import settings

MAX_RETRIES = 3
RETRY_DELAY = 2

@lru_cache(maxsize=1)
def get_longport_config() -> Config:
    return Config(
        app_key=settings.longport_app_key,
        app_secret=settings.longport_app_secret,
        access_token=settings.longport_access_token,
        # 夜盘行情:需账户在「行情商城」开通 OpenAPI 夜盘行情卡才会真有数据;
        # 无权限时 overnight_quote 返回 None(无害),开通后即插即用。
        enable_overnight=True,
    )

_trade_ctx: TradeContext | None = None
_quote_ctx: QuoteContext | None = None

def get_trade_context() -> TradeContext:
    global _trade_ctx
    if _trade_ctx is None:
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                _trade_ctx = TradeContext(get_longport_config())
                return _trade_ctx
            except Exception as exc:
                last_error = exc
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
        raise last_error  # type: ignore[misc]
    return _trade_ctx

def get_quote_context() -> QuoteContext:
    global _quote_ctx
    if _quote_ctx is None:
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                _quote_ctx = QuoteContext(get_longport_config())
                return _quote_ctx
            except Exception as exc:
                last_error = exc
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
        raise last_error  # type: ignore[misc]
    return _quote_ctx

def reset_quote_context() -> None:
    """重置行情上下文缓存，用于权限变更后刷新连接"""
    global _quote_ctx
    _quote_ctx = None