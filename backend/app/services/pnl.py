from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.position import Position
from app.schemas.pnl import PnlSummaryItem


def get_pnl_summary(db: Session, group_by: str = "symbol") -> list[PnlSummaryItem]:
    if group_by == "market":
        group_col = Position.market
    else:
        group_col = Position.symbol

    rows = (
        db.query(
            group_col.label("group_key"),
            func.sum(Position.unrealized_pnl).label("unrealized_pnl"),
            func.sum(Position.market_value).label("market_value"),
            func.sum(Position.cost_price * Position.quantity).label("cost_value"),
        )
        .group_by(group_col)
        .all()
    )

    results = []
    for row in rows:
        results.append(
            PnlSummaryItem(
                group=row.group_key or "",
                unrealized_pnl=float(row.unrealized_pnl or 0),
                market_value=float(row.market_value or 0),
                cost_value=float(row.cost_value or 0),
                total_pnl=float(row.unrealized_pnl or 0),
                realized_pnl=0.0,
            )
        )

    return sorted(results, key=lambda x: abs(x.total_pnl), reverse=True)
