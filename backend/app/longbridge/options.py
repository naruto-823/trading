"""期权链 / 期权报价封装

包了 Longbridge SDK 三层只读 API，加进程内 TTL 缓存：
- expiries(symbol) → list[date]     缓存 1 天
- chain(symbol, expiry) → list[strike]  缓存 1 小时
- option_quote(symbols) → list[OptionQuote]  不缓存（实时）

Greeks (delta/gamma/theta/vega) 长桥 OpenAPI 不直接返回，需求方需要时本地用 BS 算。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, asdict
from datetime import date
from typing import Any

EXPIRY_TTL = 24 * 3600
CHAIN_TTL = 60 * 60


@dataclass(frozen=True)
class StrikeRow:
    strike: float
    call_symbol: str
    put_symbol: str
    standard: bool

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OptionQuoteRow:
    symbol: str
    underlying_symbol: str
    direction: str  # Call / Put
    contract_type: str  # American / European
    strike_price: float
    expiry_date: str  # ISO
    contract_multiplier: int
    last_done: float
    prev_close: float
    open: float
    high: float
    low: float
    volume: int
    turnover: float
    open_interest: int
    implied_volatility: float
    historical_volatility: float
    timestamp: str
    trade_status: str

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


_lock = threading.Lock()
_expiry_cache: dict[str, tuple[list[str], float]] = {}
_chain_cache: dict[tuple[str, str], tuple[list[StrikeRow], float]] = {}


def _now() -> float:
    return time.time()


def get_expiries(symbol: str) -> list[str]:
    """返回 ISO 日期字符串列表（按升序）"""
    symbol = symbol.strip().upper()
    with _lock:
        cached = _expiry_cache.get(symbol)
        if cached and _now() - cached[1] < EXPIRY_TTL:
            return list(cached[0])

    from app.longbridge.client import get_quote_context
    ctx = get_quote_context()
    raw: list[date] = ctx.option_chain_expiry_date_list(symbol)
    iso = sorted({d.isoformat() for d in raw})
    with _lock:
        _expiry_cache[symbol] = (iso, _now())
    return iso


def get_chain(symbol: str, expiry_iso: str) -> list[StrikeRow]:
    """指定到期日的全部 strikes，含 call/put symbol"""
    symbol = symbol.strip().upper()
    key = (symbol, expiry_iso)
    with _lock:
        cached = _chain_cache.get(key)
        if cached and _now() - cached[1] < CHAIN_TTL:
            return list(cached[0])

    from app.longbridge.client import get_quote_context
    ctx = get_quote_context()
    expiry = date.fromisoformat(expiry_iso)
    raw = ctx.option_chain_info_by_date(symbol, expiry)
    rows: list[StrikeRow] = []
    for r in raw:
        rows.append(StrikeRow(
            strike=float(r.price),
            call_symbol=str(r.call_symbol or ""),
            put_symbol=str(r.put_symbol or ""),
            standard=bool(getattr(r, "standard", True)),
        ))
    rows.sort(key=lambda x: x.strike)
    with _lock:
        _chain_cache[key] = (rows, _now())
    return rows


def get_option_quotes(symbols: list[str]) -> list[OptionQuoteRow]:
    """期权实时报价（含 IV / HV / OI），需 USOption 行情包。"""
    if not symbols:
        return []
    from app.longbridge.client import get_quote_context
    ctx = get_quote_context()
    raw = ctx.option_quote(symbols)
    rows: list[OptionQuoteRow] = []
    for q in raw:
        rows.append(OptionQuoteRow(
            symbol=str(q.symbol),
            underlying_symbol=str(q.underlying_symbol),
            direction=str(q.direction).rsplit(".", 1)[-1],
            contract_type=str(q.contract_type).rsplit(".", 1)[-1],
            strike_price=float(q.strike_price),
            expiry_date=q.expiry_date.isoformat() if q.expiry_date else "",
            contract_multiplier=int(q.contract_multiplier or 0),
            last_done=float(q.last_done or 0),
            prev_close=float(q.prev_close or 0),
            open=float(q.open or 0),
            high=float(q.high or 0),
            low=float(q.low or 0),
            volume=int(q.volume or 0),
            turnover=float(q.turnover or 0),
            open_interest=int(q.open_interest or 0),
            implied_volatility=float(q.implied_volatility or 0),
            historical_volatility=float(q.historical_volatility or 0),
            timestamp=str(q.timestamp) if q.timestamp else "",
            trade_status=str(q.trade_status).rsplit(".", 1)[-1],
        ))
    return rows


def clear_cache() -> None:
    """测试 / 权限变更后调用，清空所有缓存。"""
    with _lock:
        _expiry_cache.clear()
        _chain_cache.clear()
