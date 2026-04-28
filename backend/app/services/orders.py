from datetime import datetime

from sqlalchemy.orm import Session

from app.models.order import Order
from app.schemas.order import OrderResponse


def list_orders(
    db: Session,
    symbol: str | None = None,
    status: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    page: int = 1,
    size: int = 50,
) -> tuple[list[OrderResponse], int]:
    query = db.query(Order)

    if symbol:
        query = query.filter(Order.symbol.ilike(f"%{symbol}%"))
    if status:
        query = query.filter(Order.status == status)
    if from_date:
        query = query.filter(Order.submitted_at >= from_date)
    if to_date:
        query = query.filter(Order.submitted_at <= to_date)

    total = query.count()
    offset = (page - 1) * size
    items = query.order_by(Order.submitted_at.desc()).offset(offset).limit(size).all()

    return [OrderResponse.model_validate(o) for o in items], total
