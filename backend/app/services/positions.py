from sqlalchemy.orm import Session

from app.models.position import Position
from app.schemas.position import PositionResponse


def list_positions(db: Session, market: str | None = None) -> list[PositionResponse]:
    query = db.query(Position)
    if market:
        markets = [m.strip().upper() for m in market.split(",")]
        query = query.filter(Position.market.in_(markets))
    query = query.order_by(Position.market_value.desc())
    return [PositionResponse.model_validate(p) for p in query.all()]
