from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.suggestions import build_suggestions

router = APIRouter()


@router.get("/decisions/suggestions")
def get_suggestions(
    force_refresh: bool = Query(False, description="强制跳过缓存"),
    db: Session = Depends(get_db),
):
    data = build_suggestions(db, force_refresh=force_refresh)
    return {"data": data, "error": None}
