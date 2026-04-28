from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.executions import list_executions
from app.services.orders import list_orders
from app.services.pnl import get_pnl_summary
from app.services.positions import list_positions

router = APIRouter()


@router.get("/positions")
def get_positions(market: str | None = Query(None), db: Session = Depends(get_db)):
    items = list_positions(db, market=market)
    return {"data": [item.model_dump() for item in items], "error": None}


@router.get("/executions")
def get_executions(
    symbol: str | None = Query(None),
    from_date: datetime | None = Query(None, alias="from"),
    to_date: datetime | None = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    items, total = list_executions(db, symbol=symbol, from_date=from_date, to_date=to_date, page=page, size=size)
    return {"data": {"items": [item.model_dump() for item in items], "total": total, "page": page, "size": size}, "error": None}


@router.get("/orders")
def get_orders(
    symbol: str | None = Query(None),
    status: str | None = Query(None),
    from_date: datetime | None = Query(None, alias="from"),
    to_date: datetime | None = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    items, total = list_orders(db, symbol=symbol, status=status, from_date=from_date, to_date=to_date, page=page, size=size)
    return {"data": {"items": [item.model_dump() for item in items], "total": total, "page": page, "size": size}, "error": None}


@router.get("/pnl/summary")
def get_pnl(group_by: str = Query("symbol"), db: Session = Depends(get_db)):
    items = get_pnl_summary(db, group_by=group_by)
    return {"data": [item.model_dump() for item in items], "error": None}
