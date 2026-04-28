from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.longbridge.sync import sync_account, sync_all, sync_executions, sync_orders, sync_positions
from app.models.sync_log import SyncLog
from app.schemas.sync import SyncLogResponse

router = APIRouter()

SYNC_HANDLERS = {
    "account": sync_account,
    "positions": sync_positions,
    "orders": sync_orders,
    "executions": sync_executions,
}


@router.post("/sync/all")
def do_sync_all(db: Session = Depends(get_db)):
    logs = sync_all(db)
    results = []
    for log in logs:
        results.append({
            "kind": log.kind,
            "status": log.status,
            "rows_written": log.rows_written,
            "error": log.error,
        })
    has_error = any(r["status"] == "error" for r in results)
    return {"data": results, "error": {"code": "SYNC_PARTIAL_FAIL", "message": "部分同步失败", "retryable": True} if has_error else None}


@router.post("/sync/{kind}")
def do_sync_kind(kind: str, db: Session = Depends(get_db)):
    handler = SYNC_HANDLERS.get(kind)
    if not handler:
        return {"data": None, "error": {"code": "INVALID_KIND", "message": f"不支持的同步类型: {kind}", "retryable": False}}
    log = handler(db)
    return {
        "data": {"kind": log.kind, "status": log.status, "rows_written": log.rows_written, "error": log.error},
        "error": {"code": "SYNC_ERROR", "message": log.error, "retryable": True} if log.status == "error" else None,
    }


@router.get("/sync/logs")
def get_sync_logs(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    logs = db.query(SyncLog).order_by(SyncLog.started_at.desc()).limit(limit).all()
    return {"data": [SyncLogResponse.model_validate(log).model_dump() for log in logs], "error": None}
