from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import current_username, get_auth_db
from app.longbridge.client import reset_user_contexts
from app.services.broker_credentials import broker_credentials_configured, save_broker_credentials

router = APIRouter()


class BrokerCredentialsBody(BaseModel):
    app_key: str = Field(min_length=8, max_length=256)
    app_secret: str = Field(min_length=16, max_length=512)
    access_token: str = Field(min_length=32, max_length=4096)


@router.get("/broker/credentials")
def credentials_status(db: Session = Depends(get_auth_db)):
    username = current_username()
    if not username:
        raise HTTPException(status_code=401, detail="请先登录")
    return {"data": {"configured": broker_credentials_configured(username, db)}, "error": None}


@router.put("/broker/credentials")
def update_credentials(body: BrokerCredentialsBody, db: Session = Depends(get_auth_db)):
    username = current_username()
    if not username:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        save_broker_credentials(username, body.app_key, body.app_secret, body.access_token, db)
        reset_user_contexts(username)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"data": {"configured": True}, "error": None}
