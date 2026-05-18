from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.alert import AlertCreate, AlertUpdate
from app.services.alerts import (
    create_alert,
    delete_alert,
    list_alerts,
    update_alert,
)
from app.services.notify import is_configured, send_telegram

router = APIRouter()


@router.get("/alerts")
def get_alerts(db: Session = Depends(get_db)):
    return {"data": [a.model_dump(by_alias=False) for a in list_alerts(db)], "error": None}


@router.post("/alerts")
def post_alert(payload: AlertCreate, db: Session = Depends(get_db)):
    item = create_alert(db, payload)
    return {"data": item.model_dump(by_alias=False), "error": None}


@router.patch("/alerts/{alert_id}")
def patch_alert(alert_id: str, payload: AlertUpdate, db: Session = Depends(get_db)):
    item = update_alert(db, alert_id, payload)
    if not item:
        raise HTTPException(status_code=404, detail="alert not found")
    return {"data": item.model_dump(by_alias=False), "error": None}


@router.delete("/alerts/{alert_id}")
def remove_alert(alert_id: str):
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        ok = delete_alert(db, alert_id)
        if not ok:
            raise HTTPException(status_code=404, detail="alert not found")
        return {"data": {"id": alert_id, "deleted": True}, "error": None}
    finally:
        db.close()


@router.get("/alerts/notify/status")
def notify_status():
    """前端用：判断 Telegram 是不是配好了"""
    return {"data": {"configured": is_configured()}, "error": None}


@router.post("/alerts/notify/test")
def notify_test():
    """发条测试消息验证 Telegram 配置"""
    result = send_telegram(
        "🧪 *AI Trading 测试推送*\n如果你看到这条，说明 Telegram 配置成功。"
    )
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["detail"])
    return {"data": result, "error": None}
