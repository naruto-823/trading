from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from app.config import settings
from app.services.auth import COOKIE_NAME, authenticate, create_session

router = APIRouter()


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginBody, response: Response):
    if not authenticate(body.username, body.password):
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


@router.get("/auth/session")
def session():
    return {"data": {"username": settings.auth_username}, "error": None}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/", secure=settings.auth_cookie_secure, samesite="strict")
    return {"data": {"ok": True}, "error": None}
