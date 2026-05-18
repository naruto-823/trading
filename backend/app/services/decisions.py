"""决策日志 CRUD"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.decision import Decision
from app.schemas.decision import (
    DecisionCreate,
    DecisionResponse,
    DecisionUpdate,
)


def list_decisions(db: Session, status: str | None = None) -> list[DecisionResponse]:
    q = db.query(Decision)
    if status:
        q = q.filter(Decision.status == status)
    q = q.order_by(Decision.created_at.desc())
    return [DecisionResponse.model_validate(d) for d in q.all()]


def create_decision(db: Session, payload: DecisionCreate) -> DecisionResponse:
    decision_id = payload.id or uuid.uuid4().hex
    created_at = (
        datetime.utcfromtimestamp(payload.created_at_ms / 1000)
        if payload.created_at_ms
        else datetime.utcnow()
    )
    checklist_json = (
        json.dumps(payload.checklist.model_dump(), ensure_ascii=False)
        if payload.checklist
        else None
    )

    decision = Decision(
        id=decision_id,
        created_at=created_at,
        status="pending",
        action=payload.action,
        symbol=payload.symbol,
        qty=payload.qty,
        price=payload.price,
        thesis=payload.thesis,
        cooldown_hours=payload.cooldown_hours,
        urgent_reason=payload.urgent_reason,
        checklist_json=checklist_json,
        source=payload.source,
        source_suggestion_id=payload.source_suggestion_id,
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)
    return DecisionResponse.model_validate(decision)


def update_decision_status(
    db: Session, decision_id: str, payload: DecisionUpdate
) -> DecisionResponse | None:
    decision = db.get(Decision, decision_id)
    if not decision:
        return None
    decision.status = payload.status
    if payload.status == "executed":
        decision.executed_at = datetime.utcnow()
    elif payload.status == "pending":
        decision.executed_at = None  # 万一回退
    db.commit()
    db.refresh(decision)
    return DecisionResponse.model_validate(decision)


def delete_decision(db: Session, decision_id: str) -> bool:
    decision = db.get(Decision, decision_id)
    if not decision:
        return False
    db.delete(decision)
    db.commit()
    return True
