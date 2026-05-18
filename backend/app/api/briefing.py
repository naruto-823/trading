from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.briefing import build_briefing

router = APIRouter()


@router.get("/dashboard/briefing")
def get_briefing(
    force_refresh: bool = Query(False, description="强制跳过缓存重新生成"),
    db: Session = Depends(get_db),
):
    data = build_briefing(db, force_refresh=force_refresh)
    return {"data": data, "error": None}
