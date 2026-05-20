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

    # 起后台调度器（scheduler workers）+ Playwright jin10 实时（如启用）
    from app.db import SessionLocal
    from app.longbridge.realtime import start_realtime
    from app.services.daily_baseline import bootstrap_baseline_if_missing
    from app.workers import jin10_browser_worker
    from app.workers.scheduler import shutdown_scheduler, start_scheduler

    start_scheduler()
    jin10_browser_worker.start()  # settings.jin10_browser_realtime=False 时 no-op
    start_realtime()  # Longbridge 实时报价 push 订阅；无凭证 / 空持仓时 no-op
    # 当日资产基线（缺失则从历史 AccountSnapshot 回填，让 day_pnl 第一刻就有合理值）
    _db = SessionLocal()
    try:
        bootstrap_baseline_if_missing(_db)
    finally:
        _db.close()
    try:
        yield
    finally:
        await jin10_browser_worker.stop()
        shutdown_scheduler()


app = FastAPI(title="AI Trading", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
from app.api import account, alerts, briefing, chat, decisions, events, health, options, quotes, suggestions, sync, system, trades, ws  # noqa: E402

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(account.router, prefix="/api", tags=["account"])
app.include_router(trades.router, prefix="/api", tags=["trades"])
app.include_router(quotes.router, prefix="/api", tags=["quotes"])
app.include_router(options.router, prefix="/api", tags=["options"])
app.include_router(sync.router, prefix="/api", tags=["sync"])
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(ws.router, prefix="/api", tags=["ws"])
app.include_router(briefing.router, prefix="/api", tags=["briefing"])
app.include_router(suggestions.router, prefix="/api", tags=["suggestions"])
app.include_router(decisions.router, prefix="/api", tags=["decisions"])
app.include_router(system.router, prefix="/api", tags=["system"])
app.include_router(alerts.router, prefix="/api", tags=["alerts"])
app.include_router(events.router, prefix="/api", tags=["events"])
