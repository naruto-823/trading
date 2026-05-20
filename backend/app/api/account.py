from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.account import get_latest_account
from app.services.daily_baseline import compute_day_pnl

router = APIRouter()


@router.get("/account")
def get_account(db: Session = Depends(get_db)):
    snapshot = get_latest_account(db)
    if not snapshot:
        return {"data": None, "error": {"code": "NO_DATA", "message": "暂无账户数据，请先同步", "retryable": True}}
    payload = snapshot.model_dump()
    # 基线口径的当日盈亏 — 对齐 LB APP 的"当日盈亏"显示，因为 LB OpenAPI 不直接返回该字段
    payload["day_pnl_baseline"] = compute_day_pnl(db)
    return {"data": payload, "error": None}


@router.post("/account/baseline/recapture")
def recapture_baseline(db: Session = Depends(get_db)):
    """强制重抓今日基线（调试 / 用户手动校准用）"""
    from app.services.daily_baseline import capture_baseline
    row = capture_baseline(db, force=True)
    if not row:
        return {"data": None, "error": {"code": "NO_SNAPSHOT", "message": "暂无账户快照可作基线", "retryable": True}}
    return {
        "data": {
            "baseline_key": row.baseline_key,
            "captured_at": row.captured_at.isoformat(),
            "net_assets_hkd": row.net_assets_hkd,
            "source": row.source,
        },
        "error": None,
    }
