from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.suggestions import (
    build_suggestions,
    dismiss_suggestion,
    list_suggestion_history,
)

router = APIRouter()


@router.get("/decisions/suggestions")
def get_suggestions(
    force_refresh: bool = Query(False, description="强制跳过缓存"),
    db: Session = Depends(get_db),
):
    data = build_suggestions(db, force_refresh=force_refresh)
    return {"data": data, "error": None}


@router.get("/decisions/suggestions/history")
def get_suggestion_history(
    days: int = Query(7, ge=1, le=90, description="时间窗（天）"),
    db: Session = Depends(get_db),
):
    batches = list_suggestion_history(db, days=days)
    return {"data": batches, "error": None}


@router.post("/decisions/suggestions/{row_id}/dismiss")
def post_dismiss_suggestion(row_id: str, db: Session = Depends(get_db)):
    ok = dismiss_suggestion(db, row_id)
    if not ok:
        raise HTTPException(status_code=404, detail="suggestion not found")
    return {"data": {"row_id": row_id, "dismissed": True}, "error": None}
