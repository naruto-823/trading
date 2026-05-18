"""告警规则 CRUD + 触发检查"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.schemas.alert import AlertCreate, AlertResponse, AlertUpdate

logger = logging.getLogger(__name__)


def list_alerts(db: Session) -> list[AlertResponse]:
    rows = db.query(Alert).order_by(Alert.created_at.desc()).all()
    return [AlertResponse.model_validate(r) for r in rows]


def create_alert(db: Session, payload: AlertCreate) -> AlertResponse:
    alert = Alert(
        id=uuid.uuid4().hex,
        created_at=datetime.utcnow(),
        enabled=payload.enabled,
        symbol=payload.symbol.upper(),
        condition=payload.condition,
        threshold=payload.threshold,
        note=payload.note,
        cooldown_minutes=payload.cooldown_minutes,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return AlertResponse.model_validate(alert)


def update_alert(
    db: Session, alert_id: str, payload: AlertUpdate
) -> AlertResponse | None:
    alert = db.get(Alert, alert_id)
    if not alert:
        return None
    for field in ("enabled", "threshold", "note", "cooldown_minutes", "condition"):
        v = getattr(payload, field)
        if v is not None:
            setattr(alert, field, v)
    if payload.reset_cooldown:
        alert.last_triggered_at = None
    db.commit()
    db.refresh(alert)
    return AlertResponse.model_validate(alert)


def delete_alert(db: Session, alert_id: str) -> bool:
    alert = db.get(Alert, alert_id)
    if not alert:
        return False
    db.delete(alert)
    db.commit()
    return True


def list_active_alerts(db: Session) -> list[Alert]:
    """market-watcher 用：拿所有 enabled 的规则"""
    return db.query(Alert).filter(Alert.enabled.is_(True)).all()


def is_in_cooldown(alert: Alert, now: datetime | None = None) -> bool:
    if alert.last_triggered_at is None:
        return False
    now = now or datetime.utcnow()
    last = alert.last_triggered_at
    if last.tzinfo is not None:
        # 入库的可能带 tz；统一成 naive utc
        last = last.replace(tzinfo=None)
    return now - last < timedelta(minutes=alert.cooldown_minutes)


def mark_triggered(db: Session, alert: Alert) -> None:
    alert.last_triggered_at = datetime.utcnow()
    alert.trigger_count += 1
    db.commit()


def check_condition(
    condition: str,
    threshold: float,
    current_price: float,
    prev_close: float,
) -> bool:
    """单条规则的命中判定。返回 True = 命中。"""
    if current_price <= 0:
        return False

    if condition == "price_above":
        return current_price > threshold
    if condition == "price_below":
        return current_price < threshold
    if condition in ("day_change_pct_above", "day_change_pct_below"):
        if prev_close <= 0:
            return False
        pct = (current_price - prev_close) / prev_close * 100
        if condition == "day_change_pct_above":
            return pct > threshold
        return pct < threshold
    return False


def format_trigger_message(alert: Alert, current_price: float, prev_close: float) -> str:
    """构造发送给 Telegram 的告警文案（Markdown）"""
    pct = (current_price - prev_close) / prev_close * 100 if prev_close > 0 else 0.0

    cond_desc = {
        "price_above": f"现价突破 *${alert.threshold}*",
        "price_below": f"现价跌破 *${alert.threshold}*",
        "day_change_pct_above": f"日内涨幅突破 *+{alert.threshold}%*",
        "day_change_pct_below": f"日内跌幅突破 *{alert.threshold}%*",
    }.get(alert.condition, alert.condition)

    note_part = f"\n*备注*: {alert.note}" if alert.note else ""

    return (
        f"🔔 *{alert.symbol} 触发告警*\n"
        f"\n"
        f"*条件*: {cond_desc}\n"
        f"*现价*: ${current_price:.2f} ({pct:+.2f}%)\n"
        f"*昨收*: ${prev_close:.2f}"
        f"{note_part}"
    )
