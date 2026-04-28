from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    init_db()
    if not settings.validate_longport():
        print("⚠️  长桥 API 凭证未配置，请编辑 .env 文件（参考 .env.example）")
    if not settings.validate_anthropic():
        print("⚠️  Anthropic API Key 未配置，AI 对话功能不可用")
    yield


app = FastAPI(title="AI Trading", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
from app.api import account, chat, health, quotes, sync, trades, ws  # noqa: E402

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(account.router, prefix="/api", tags=["account"])
app.include_router(trades.router, prefix="/api", tags=["trades"])
app.include_router(quotes.router, prefix="/api", tags=["quotes"])
app.include_router(sync.router, prefix="/api", tags=["sync"])
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(ws.router, prefix="/api", tags=["ws"])
