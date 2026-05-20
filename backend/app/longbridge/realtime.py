"""Longbridge 实时报价订阅 + 内存缓存

工作方式：
1. 启动时从 DB 拉持仓 symbol 列表，调 QuoteContext.subscribe([...], [SubType.Quote])
2. 注册 set_on_quote(callback) 回调；SDK 在内部线程触发回调，回调把 PushQuote 写入 store
3. 外部消费者（WS push / market_watcher_worker / quote service）通过 get_quote(symbol)
   读取最新 push 数据；如果某 symbol 没 push 过则返回 None。
4. /api/sync/all 完成后调 refresh()，增量订阅新持仓 / 退订已平仓的。

线程安全：用 threading.RLock 保护 store 和订阅集合。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, asdict
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RealtimeQuote:
    symbol: str
    last_done: float
    open: float
    high: float
    low: float
    volume: int          # 累计成交量
    turnover: float      # 累计成交额
    current_volume: int  # 本次 push 增量成交量
    current_turnover: float  # 本次 push 增量成交额
    trade_status: str
    trade_session: str   # Intraday / Pre / Post / Overnight
    timestamp: str       # ISO
    received_at: float   # 服务端收到时间（time.time()）
    source: str = "longbridge_ws"  # longbridge_ws / nasdaq_overnight

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


class _RealtimeStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._store: dict[str, RealtimeQuote] = {}
        self._subscribed: set[str] = set()
        self._started = False
        self._callback_registered = False

    # ---- 读 ----
    def get(self, symbol: str) -> RealtimeQuote | None:
        with self._lock:
            return self._store.get(symbol)

    def snapshot(self) -> dict[str, RealtimeQuote]:
        with self._lock:
            return dict(self._store)

    def subscribed_symbols(self) -> list[str]:
        with self._lock:
            return sorted(self._subscribed)

    def is_started(self) -> bool:
        return self._started

    # ---- 写（SDK 回调走这里）----
    def _on_push(self, symbol: str, push) -> None:
        try:
            q = RealtimeQuote(
                symbol=str(symbol),
                last_done=float(push.last_done or 0),
                open=float(push.open or 0),
                high=float(push.high or 0),
                low=float(push.low or 0),
                volume=int(push.volume or 0),
                turnover=float(push.turnover or 0),
                current_volume=int(push.current_volume or 0),
                current_turnover=float(push.current_turnover or 0),
                trade_status=str(push.trade_status).rsplit(".", 1)[-1],
                trade_session=str(push.trade_session).rsplit(".", 1)[-1],
                timestamp=str(push.timestamp) if push.timestamp else "",
                received_at=time.time(),
                source="longbridge_ws",
            )
            with self._lock:
                self._store[str(symbol)] = q
        except Exception as exc:
            logger.warning("realtime push parse failed for %s: %s", symbol, exc)

    # ---- 写（overnight 兜底用：把 Nasdaq 拉来的报价塞进 store）----
    def update_from_overnight(
        self,
        symbol: str,
        last_done: float,
        prev_close: float,
        timestamp_iso: str = "",
    ) -> None:
        """美股 overnight 时段从 Nasdaq API 拉来的价格写入 store。
        Nasdaq API 不提供 OHLCV，只填 last_done；其他字段保留 0 或继承旧值。
        """
        if last_done <= 0:
            return
        with self._lock:
            prev = self._store.get(symbol)
            q = RealtimeQuote(
                symbol=symbol,
                last_done=float(last_done),
                open=prev.open if prev else 0.0,
                high=prev.high if prev else 0.0,
                low=prev.low if prev else 0.0,
                volume=prev.volume if prev else 0,
                turnover=prev.turnover if prev else 0.0,
                current_volume=0,
                current_turnover=0.0,
                trade_status="Normal",
                trade_session="Overnight",
                timestamp=timestamp_iso or (prev.timestamp if prev else ""),
                received_at=time.time(),
                source="nasdaq_overnight",
            )
            self._store[symbol] = q

    # ---- 启动 / 刷新 ----
    def start(self, symbols: list[str]) -> None:
        """首次启动：注册回调 + 订阅初始 symbol 列表。可重复调用（幂等）"""
        if not settings.validate_longport():
            logger.info("Longbridge not configured, realtime store disabled")
            return
        symbols = [s for s in dict.fromkeys(symbols) if s]
        from app.longbridge.client import get_quote_context
        from longport.openapi import SubType

        ctx = get_quote_context()
        with self._lock:
            if not self._callback_registered:
                ctx.set_on_quote(self._on_push)
                self._callback_registered = True
            new = [s for s in symbols if s not in self._subscribed]
            if new:
                try:
                    ctx.subscribe(new, [SubType.Quote])
                    self._subscribed.update(new)
                    logger.info("realtime subscribed: %s (total %d)", new, len(self._subscribed))
                except Exception as exc:
                    logger.warning("realtime subscribe failed: %s symbols=%s", exc, new)
            self._started = True

    def refresh(self, symbols: list[str]) -> None:
        """增量同步订阅：新增订阅 + 取消已不在列表的（保持 store 与持仓一致）"""
        if not self._started:
            self.start(symbols)
            return
        if not settings.validate_longport():
            return
        symbols_set = {s for s in symbols if s}
        from app.longbridge.client import get_quote_context
        from longport.openapi import SubType

        ctx = get_quote_context()
        with self._lock:
            to_add = sorted(symbols_set - self._subscribed)
            to_remove = sorted(self._subscribed - symbols_set)
            if to_add:
                try:
                    ctx.subscribe(to_add, [SubType.Quote])
                    self._subscribed.update(to_add)
                    logger.info("realtime +subscribe: %s", to_add)
                except Exception as exc:
                    logger.warning("realtime subscribe failed: %s symbols=%s", exc, to_add)
            if to_remove:
                try:
                    ctx.unsubscribe(to_remove, [SubType.Quote])
                    self._subscribed.difference_update(to_remove)
                    for s in to_remove:
                        self._store.pop(s, None)
                    logger.info("realtime -unsubscribe: %s", to_remove)
                except Exception as exc:
                    logger.warning("realtime unsubscribe failed: %s symbols=%s", exc, to_remove)


# 单例
store = _RealtimeStore()


def _collect_active_symbols() -> list[str]:
    """从 DB 拉当前持仓的 symbol 列表（去重）。"""
    from app.db import SessionLocal
    from app.models.position import Position

    syms: list[str] = []
    db = SessionLocal()
    try:
        for p in db.query(Position).all():
            if p.symbol and p.market_value:
                syms.append(p.symbol)
    finally:
        db.close()
    # 去重保持顺序
    return list(dict.fromkeys(syms))


def start_realtime() -> None:
    """app 启动时调一次。失败不抛，仅 log。"""
    try:
        symbols = _collect_active_symbols()
        if not symbols:
            logger.info("realtime: no positions in DB, skip initial subscribe")
            return
        store.start(symbols)
    except Exception as exc:
        logger.warning("start_realtime failed: %s", exc)


def refresh_realtime_subscriptions() -> None:
    """sync 完成后调，让订阅集跟持仓保持一致。"""
    try:
        symbols = _collect_active_symbols()
        store.refresh(symbols)
    except Exception as exc:
        logger.warning("refresh_realtime_subscriptions failed: %s", exc)
