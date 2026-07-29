from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.risk_radar import build_risk_radar

router = APIRouter()


@router.get("/risk/radar")
def get_risk_radar(
    shock_pct: float = Query(10, ge=1, le=80, description="股票压力测试跌幅百分比"),
    db: Session = Depends(get_db),
):
    return {"data": build_risk_radar(db, shock_pct), "error": None}
