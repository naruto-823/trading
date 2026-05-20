"""汇率单一来源（FX source of truth）

优先级（高到低）：
1. 最新 AccountSnapshot.fx_rates — LB 同步时落库的实时汇率（每次同步刷新）
2. sync._get_fx_rates() — open.er-api.com 在线 API（带进程内缓存）
3. 最终兜底硬编值 — 仅当 LB 没同步过 + 在线 API 也挂了

绝大多数情况下走 #1，业务层不该再出现硬编的 7.83 / 0.87。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 兜底值：仅当 LB 没同步过 + 在线 API 全挂时使用，绝对值不应该被业务逻辑依赖
_LAST_RESORT = {
    "USD_HKD": 7.8315,
    "USD_CNY": 7.24,
    "HKD_CNY": 0.92,
    "CNY_HKD": 1.08,
}
# 进程内缓存：避免每次换算都查 DB
_cache: dict[str, float] | None = None
_cache_ts: float = 0.0
_CACHE_TTL_SECONDS = 60  # 1 分钟内的换算共用一份；下次同步后会被刷新


def _from_account_snapshot(db: Session | None) -> dict[str, float] | None:
    if db is None:
        return None
    from app.models.account import AccountSnapshot
    snap = (
        db.query(AccountSnapshot)
        .order_by(AccountSnapshot.synced_at.desc())
        .first()
    )
    if snap is None or not snap.fx_rates_json:
        return None
    import json
    try:
        rates = json.loads(snap.fx_rates_json)
    except Exception:
        return None
    if not isinstance(rates, dict) or "USD_HKD" not in rates:
        return None
    return {k: float(v) for k, v in rates.items() if isinstance(v, (int, float))}


def _from_online_api() -> dict[str, float] | None:
    """走 sync._get_fx_rates()（它内部已带 5min 缓存 + open.er-api.com 拉取）"""
    try:
        from app.longbridge.sync import _get_fx_rates
        return _get_fx_rates()
    except Exception as exc:
        logger.warning("fx: online API call failed: %s", exc)
        return None


def get_rates(db: Session | None = None, *, refresh: bool = False) -> dict[str, float]:
    """返回完整汇率字典：USD_HKD / USD_CNY / HKD_CNY / CNY_HKD

    db 不传时跳过 AccountSnapshot 查询，直接走在线 API。
    refresh=True 时绕过缓存。
    """
    global _cache, _cache_ts
    import time as _t

    if not refresh and _cache is not None and (_t.time() - _cache_ts) < _CACHE_TTL_SECONDS:
        return dict(_cache)

    rates = _from_account_snapshot(db) or _from_online_api() or dict(_LAST_RESORT)
    # 缺失补齐
    if "USD_HKD" not in rates:
        rates["USD_HKD"] = _LAST_RESORT["USD_HKD"]
    if "USD_CNY" not in rates:
        rates["USD_CNY"] = _LAST_RESORT["USD_CNY"]
    usd_hkd = rates["USD_HKD"]
    usd_cny = rates["USD_CNY"]
    rates.setdefault("HKD_CNY", usd_cny / usd_hkd if usd_hkd else _LAST_RESORT["HKD_CNY"])
    rates.setdefault("CNY_HKD", usd_hkd / usd_cny if usd_cny else _LAST_RESORT["CNY_HKD"])

    _cache = dict(rates)
    _cache_ts = _t.time()
    return dict(rates)


def usd_to_hkd(db: Session | None = None) -> float:
    return get_rates(db).get("USD_HKD", _LAST_RESORT["USD_HKD"])


def hkd_to_cny(db: Session | None = None) -> float:
    return get_rates(db).get("HKD_CNY", _LAST_RESORT["HKD_CNY"])


def to_hkd(value: float, currency: str, db: Session | None = None) -> float:
    """通用换算：把 value（指定币种）转 HKD"""
    if value == 0:
        return 0.0
    cur = (currency or "").upper()
    if cur == "HKD":
        return float(value)
    if cur == "USD":
        return float(value) * usd_to_hkd(db)
    if cur == "CNY" or cur == "CNH":
        rates = get_rates(db)
        cny_hkd = rates.get("CNY_HKD") or (1 / rates["HKD_CNY"] if rates.get("HKD_CNY") else _LAST_RESORT["CNY_HKD"])
        return float(value) * cny_hkd
    # 未知币种：原样返回
    return float(value)


def reset_cache() -> None:
    """sync 完成后调用，让下一次取值重新读 AccountSnapshot"""
    global _cache, _cache_ts
    _cache = None
    _cache_ts = 0.0
