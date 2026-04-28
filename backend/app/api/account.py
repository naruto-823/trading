from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.account import get_latest_account

router = APIRouter()


@router.get("/account")
def get_account(db: Session = Depends(get_db)):
    snapshot = get_latest_account(db)
    if not snapshot:
        return {"data": None, "error": {"code": "NO_DATA", "message": "暂无账户数据，请先同步", "retryable": True}}
    return {"data": snapshot.model_dump(), "error": None}
