from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.event_watcher import list_recent_events

router = APIRouter()


@router.get("/events")
def get_events(
    days: int = Query(7, ge=1, le=60, description="时间窗（天）"),
    db: Session = Depends(get_db),
):
    rows = list_recent_events(db, days=days)
    return {
        "data": [
            {
                "id": r.id,
                "notified_at_ms": int(r.notified_at.timestamp() * 1000) if r.notified_at else 0,
                "symbol": r.symbol,
                "importance": r.importance,
                "title": r.title,
                "body": r.body,
                "source_title": r.source_title,
                "push_status": r.push_status,
                "push_error": r.push_error,
            }
            for r in rows
        ],
        "error": None,
    }
