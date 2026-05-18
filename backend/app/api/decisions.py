from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.decision import DecisionCreate, DecisionUpdate
from app.services.decisions import (
    create_decision,
    delete_decision,
    list_decisions,
    update_decision_status,
)

router = APIRouter()


@router.get("/decisions")
def get_decisions(
    status: str | None = Query(None, description="按状态过滤：pending / executed / abandoned"),
    db: Session = Depends(get_db),
):
    items = list_decisions(db, status=status)
    return {"data": [it.model_dump(by_alias=False) for it in items], "error": None}


@router.post("/decisions")
def post_decision(payload: DecisionCreate, db: Session = Depends(get_db)):
    item = create_decision(db, payload)
    return {"data": item.model_dump(by_alias=False), "error": None}


@router.patch("/decisions/{decision_id}")
def patch_decision(
    decision_id: str,
    payload: DecisionUpdate,
    db: Session = Depends(get_db),
):
    item = update_decision_status(db, decision_id, payload)
    if not item:
        raise HTTPException(status_code=404, detail="decision not found")
    return {"data": item.model_dump(by_alias=False), "error": None}


@router.delete("/decisions/{decision_id}")
def remove_decision(decision_id: str, db: Session = Depends(get_db)):
    ok = delete_decision(db, decision_id)
    if not ok:
        raise HTTPException(status_code=404, detail="decision not found")
    return {"data": {"id": decision_id, "deleted": True}, "error": None}
