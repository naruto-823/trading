"""每日净资产基线 — capture / lookup / day_pnl 算法

对齐 Longbridge APP 的"当日盈亏"口径：
- 日切点 = 北京时间 16:00（港股收盘）
- 经验对齐：用 BJT 16:00 boundary 算出的 day_pnl 与 LB APP 显示最接近
  （LB 是港股券商，账户主币 HKD，按港股交易日日切是合理的）
- baseline_key：当前 BJT 时刻 ≥ 16:00 → 用今天日期；否则 → 用昨天日期
- day_pnl = current_net_assets − today_baseline.net_assets
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.account import AccountSnapshot
from app.models.daily_baseline import DailyBaseline

logger = logging.getLogger(__name__)

# 日切点：北京时间 16:00（HK 收盘）= UTC 08:00
DAY_BOUNDARY_BJT_HOUR = 16


def current_baseline_key(now_utc: datetime | None = None) -> str:
    """返回当前时刻所属的 baseline_key（YYYY-MM-DD，北京日，按 BJT 16:00 日切）

    规则：BJT < 16:00 → baseline_key 是昨天的（昨天 16:00 是 boundary）
         BJT ≥ 16:00 → baseline_key 是今天的（今天 16:00 是 boundary）
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    beijing = now_utc + timedelta(hours=8)
    if beijing.hour < DAY_BOUNDARY_BJT_HOUR:
        beijing -= timedelta(days=1)
    return beijing.strftime("%Y-%m-%d")


def boundary_utc_for_key(baseline_key: str) -> datetime:
    """给定 baseline_key 返回 boundary 的 UTC 时刻

    baseline_key=2026-05-18 → BJT 16:00 on 2026-05-18 = UTC 08:00 on 2026-05-18
    """
    d = datetime.strptime(baseline_key, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return d.replace(hour=DAY_BOUNDARY_BJT_HOUR) - timedelta(hours=8)


def get_current_baseline(db: Session) -> DailyBaseline | None:
    key = current_baseline_key()
    return (
        db.query(DailyBaseline)
        .filter(DailyBaseline.baseline_key == key)
        .first()
    )


def _find_closest_snapshot_to(
    db: Session, target_utc: datetime, max_drift_hours: float = 12.0
) -> AccountSnapshot | None:
    """从 AccountSnapshot 历史里找时间最接近 target_utc 的快照（容忍 ±N 小时）"""
    if target_utc.tzinfo is None:
        target_utc = target_utc.replace(tzinfo=timezone.utc)
    # SQLite 存的是 naive datetime（UTC 含义），比较时用 naive
    target_naive = target_utc.replace(tzinfo=None)
    lo = target_naive - timedelta(hours=max_drift_hours)
    hi = target_naive + timedelta(hours=max_drift_hours)
    candidates = (
        db.query(AccountSnapshot)
        .filter(AccountSnapshot.synced_at >= lo, AccountSnapshot.synced_at <= hi)
        .all()
    )
    if not candidates:
        return None
    return min(candidates, key=lambda s: abs((s.synced_at - target_naive).total_seconds()))


def capture_baseline(db: Session, force: bool = False) -> DailyBaseline | None:
    """抓当前最新 AccountSnapshot 作为今日基线（幂等：今日已存在 + 非 force 直接返回）"""
    key = current_baseline_key()
    existing = (
        db.query(DailyBaseline)
        .filter(DailyBaseline.baseline_key == key)
        .first()
    )
    if existing and not force:
        return existing

    snap = (
        db.query(AccountSnapshot)
        .order_by(AccountSnapshot.synced_at.desc())
        .first()
    )
    if not snap:
        logger.info("capture_baseline: no AccountSnapshot yet, skip")
        return None

    row = existing or DailyBaseline(baseline_key=key)
    row.captured_at = datetime.utcnow()
    row.net_assets_hkd = float(snap.net_assets or 0)
    row.market_value_hkd = float(snap.market_value or 0)
    row.total_cash_hkd = float(snap.total_cash or 0)
    row.total_pnl_hkd = float(snap.total_pnl or 0)
    row.fx_rates_json = snap.fx_rates_json
    row.source = "snapshot"
    if existing is None:
        db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(
        "baseline captured: key=%s net_assets=%.2f source=%s",
        key, row.net_assets_hkd, row.source,
    )
    return row


def bootstrap_baseline_if_missing(db: Session) -> DailyBaseline | None:
    """启动时调用。如果今日基线不存在，尝试从历史 AccountSnapshot 找最接近今日 06:00 BJT 的快照
    作为 backfill；找不到再退化为当前最新快照。"""
    key = current_baseline_key()
    existing = (
        db.query(DailyBaseline)
        .filter(DailyBaseline.baseline_key == key)
        .first()
    )
    if existing:
        return existing

    target = boundary_utc_for_key(key)
    snap = _find_closest_snapshot_to(db, target, max_drift_hours=12.0)
    if snap is None:
        # 找不到接近 06:00 的快照，用当前最新作为 fallback
        snap = (
            db.query(AccountSnapshot)
            .order_by(AccountSnapshot.synced_at.desc())
            .first()
        )
    if snap is None:
        logger.info("bootstrap_baseline: no AccountSnapshot yet")
        return None

    row = DailyBaseline(
        baseline_key=key,
        captured_at=datetime.utcnow(),
        net_assets_hkd=float(snap.net_assets or 0),
        market_value_hkd=float(snap.market_value or 0),
        total_cash_hkd=float(snap.total_cash or 0),
        total_pnl_hkd=float(snap.total_pnl or 0),
        fx_rates_json=snap.fx_rates_json,
        source="backfill",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(
        "baseline backfilled: key=%s from snapshot@%s net_assets=%.2f",
        key, snap.synced_at, row.net_assets_hkd,
    )
    return row


def compute_day_pnl(db: Session) -> dict:
    """计算当前 day_pnl（HKD）+ 基线元信息，供 API 返回。"""
    baseline = get_current_baseline(db)
    snap = (
        db.query(AccountSnapshot)
        .order_by(AccountSnapshot.synced_at.desc())
        .first()
    )
    if baseline is None or snap is None:
        return {
            "day_pnl_hkd": 0.0,
            "baseline_key": None,
            "baseline_captured_at": None,
            "baseline_net_assets_hkd": 0.0,
            "baseline_source": None,
        }
    return {
        "day_pnl_hkd": float(snap.net_assets or 0) - float(baseline.net_assets_hkd),
        "baseline_key": baseline.baseline_key,
        "baseline_captured_at": baseline.captured_at.replace(tzinfo=timezone.utc).isoformat()
            if baseline.captured_at.tzinfo is None else baseline.captured_at.isoformat(),
        "baseline_net_assets_hkd": float(baseline.net_assets_hkd),
        "baseline_source": baseline.source,
    }
