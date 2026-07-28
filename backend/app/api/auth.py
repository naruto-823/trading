import hmac
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models.user import User
from app.services.auth import COOKIE_NAME, authenticate, create_session, hash_password, session_username

router = APIRouter()


class LoginBody(BaseModel):
    username: str
    password: str


class RegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=10, max_length=128)
    invitation_code: str = Field(min_length=1, max_length=128)


@router.post("/auth/login")
def login(body: LoginBody, response: Response, db: Session = Depends(get_db)):
    if not authenticate(body.username, body.password, db):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    response.set_cookie(
        COOKIE_NAME,
        create_session(body.username),
        max_age=settings.auth_session_hours * 3600,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="strict",
        path="/",
    )
    return {"data": {"username": body.username}, "error": None}


@router.post("/auth/register", status_code=201)
def register(body: RegisterBody, response: Response, db: Session = Depends(get_db)):
    if not settings.auth_registration_code or not hmac.compare_digest(
        body.invitation_code, settings.auth_registration_code
    ):
        raise HTTPException(status_code=403, detail="邀请码无效")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", body.username):
        raise HTTPException(status_code=422, detail="用户名只能包含字母、数字、下划线和连字符")
    if hmac.compare_digest(body.username, settings.auth_username) or db.scalar(
        select(User.id).where(User.username == body.username)
    ):
        raise HTTPException(status_code=409, detail="用户名已存在")

    salt, password_hash = hash_password(body.password)
    db.add(User(username=body.username, password_salt=salt, password_hash=password_hash))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="用户名已存在") from None

    response.set_cookie(
        COOKIE_NAME,
        create_session(body.username),
        max_age=settings.auth_session_hours * 3600,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="strict",
        path="/",
    )
    return {"data": {"username": body.username}, "error": None}


@router.get("/auth/session")
def session(request: Request):
    return {"data": {"username": session_username(request.cookies.get(COOKIE_NAME))}, "error": None}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/", secure=settings.auth_cookie_secure, samesite="strict")
    return {"data": {"ok": True}, "error": None}
