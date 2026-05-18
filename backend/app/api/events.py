import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.event_watcher import list_recent_events

router = APIRouter()


@router.get("/events")
def get_events(
    days: int = Query(7, ge=1, le=60, description="时间窗（天）"),
    include_skipped: bool = Query(True, description="是否包含 skipped_low_relevance 的"),
    db: Session = Depends(get_db),
):
    rows = list_recent_events(db, days=days)
    if not include_skipped:
        rows = [r for r in rows if r.push_status != "skipped_low_relevance"]

    items = []
    for r in rows:
        affected = []
        if r.affected_tickers_json:
            try:
                affected = json.loads(r.affected_tickers_json)
            except Exception:
                pass
        items.append({
            "id": r.id,
            "notified_at_ms": int(r.notified_at.timestamp() * 1000) if r.notified_at else 0,
            "symbol": r.symbol,
            "importance": r.importance,
            "title": r.title,
            "body": r.body,
            "source_title": r.source_title,
            "push_status": r.push_status,
            "push_error": r.push_error,
            # 多维度评分
            "relevance": r.relevance,
            "relevance_score": r.relevance_score,
            "relevance_reason": r.relevance_reason,
            "sentiment": r.sentiment,
            "direction": r.direction,
            "confidence": r.confidence,
            "affected_tickers": affected,
        })
    return {"data": items, "error": None}
