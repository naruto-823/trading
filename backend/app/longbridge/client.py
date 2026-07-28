"""长桥 OpenAPI 客户端单例管理（带重试）"""

import time
from functools import lru_cache

from longport.openapi import Config, QuoteContext, TradeContext

from app.config import settings
from app.db import current_username
from app.services.broker_credentials import get_broker_credentials

MAX_RETRIES = 3
RETRY_DELAY = 2

def _active_username() -> str:
    return current_username() or settings.auth_username


@lru_cache(maxsize=128)
def _build_longport_config(app_key: str, app_secret: str, access_token: str) -> Config:
    return Config(
        app_key=app_key,
        app_secret=app_secret,
        access_token=access_token,
        # 夜盘行情:需账户在「行情商城」开通 OpenAPI 夜盘行情卡才会真有数据;
        # 无权限时 overnight_quote 返回 None(无害),开通后即插即用。
        enable_overnight=True,
    )


def get_longport_config() -> Config:
    return _build_longport_config(*get_broker_credentials(_active_username()))


_trade_contexts: dict[str, TradeContext] = {}
_quote_contexts: dict[str, QuoteContext] = {}

def get_trade_context() -> TradeContext:
    username = _active_username()
    if username not in _trade_contexts:
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                _trade_contexts[username] = TradeContext(get_longport_config())
                return _trade_contexts[username]
            except Exception as exc:
                last_error = exc
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
        raise last_error  # type: ignore[misc]
    return _trade_contexts[username]

def get_quote_context() -> QuoteContext:
    username = _active_username()
    if username not in _quote_contexts:
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                _quote_contexts[username] = QuoteContext(get_longport_config())
                return _quote_contexts[username]
            except Exception as exc:
                last_error = exc
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
        raise last_error  # type: ignore[misc]
    return _quote_contexts[username]

def reset_quote_context() -> None:
    """重置行情上下文缓存，用于权限变更后刷新连接"""
    _quote_contexts.pop(_active_username(), None)


def reset_user_contexts(username: str) -> None:
    _trade_contexts.pop(username, None)
    _quote_contexts.pop(username, None)
