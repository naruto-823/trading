from datetime import datetime

from sqlalchemy.orm import Session

from app.models.execution import Execution
from app.schemas.execution import ExecutionResponse


def list_executions(
    db: Session,
    symbol: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    page: int = 1,
    size: int = 50,
) -> tuple[list[ExecutionResponse], int]:
    query = db.query(Execution)

    if symbol:
        query = query.filter(Execution.symbol.ilike(f"%{symbol}%"))
    if from_date:
        query = query.filter(Execution.trade_done_at >= from_date)
    if to_date:
        query = query.filter(Execution.trade_done_at <= to_date)

    total = query.count()
    offset = (page - 1) * size
    items = query.order_by(Execution.trade_done_at.desc()).offset(offset).limit(size).all()

    return [ExecutionResponse.model_validate(e) for e in items], total
