from fastapi import APIRouter, HTTPException

from app.services.macro_feed import available_macro_sources
from app.services.news_sources import available_sources
from app.workers import jin10_browser_worker
from app.workers.scheduler import list_jobs, trigger_job_now

router = APIRouter()


@router.get("/system/jobs")
def get_jobs():
    """列出所有后台 job 的状态（next_run / last_run / last_status）"""
    return {"data": list_jobs(), "error": None}


@router.post("/system/jobs/{job_id}/run")
async def post_run_job(job_id: str):
    """手动触发某个 job 立即跑一次"""
    ok = await trigger_job_now(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return {"data": {"job_id": job_id, "triggered": True}, "error": None}


@router.get("/system/news-sources")
def get_news_sources():
    """查看新闻源：per-stock fallback 链 + 中文宏观流"""
    return {
        "data": {
            "per_stock": available_sources(),
            "macro_zh": available_macro_sources(),
        },
        "error": None,
    }


@router.get("/system/jin10-browser")
def get_jin10_browser():
    """金十实时浏览器 worker 状态（Playwright headless chromium）"""
    return {"data": jin10_browser_worker.status(), "error": None}


@router.get("/system/realtime-quotes")
def get_realtime_status():
    """Longbridge 实时报价 push 订阅状态 + 最新 cache 快照"""
    from app.longbridge.realtime import store

    snap = store.snapshot()
    return {
        "data": {
            "started": store.is_started(),
            "subscribed": store.subscribed_symbols(),
            "received": [q.model_dump() for q in snap.values()],
        },
        "error": None,
    }


@router.get("/system/longbridge-packages")
def get_longbridge_packages():
    """LB 行情包到期状态（提前续费提醒用）"""
    from datetime import datetime, timezone
    from app.config import settings
    from app.longbridge.client import get_quote_context

    if not settings.validate_longport():
        return {"data": None, "error": {"code": "NO_LB_CREDS", "message": "Longbridge 未配置", "retryable": False}}

    try:
        ctx = get_quote_context()
        details = ctx.quote_package_details()
        now = datetime.now(timezone.utc)
        rows = []
        for d in details:
            # SDK 返回 datetime（naive，按 UTC 解读）
            end_at = getattr(d, "end_at", None)
            start_at = getattr(d, "start_at", None)
            days_left: float | None = None
            if isinstance(end_at, datetime):
                end_utc = end_at if end_at.tzinfo else end_at.replace(tzinfo=timezone.utc)
                days_left = (end_utc - now).total_seconds() / 86400
            rows.append({
                "key": str(getattr(d, "key", "")),
                "name": str(getattr(d, "name", "")),
                "description": str(getattr(d, "description", "")),
                "start_at": start_at.isoformat() if isinstance(start_at, datetime) else str(start_at or ""),
                "end_at": end_at.isoformat() if isinstance(end_at, datetime) else str(end_at or ""),
                "days_left": round(days_left, 2) if days_left is not None else None,
                "expiring_soon": days_left is not None and days_left < 7,
            })
        rows.sort(key=lambda r: r["days_left"] if r["days_left"] is not None else 1e9)
        return {"data": rows, "error": None}
    except Exception as exc:
        return {"data": None, "error": {"code": "LB_PACKAGE_ERROR", "message": str(exc), "retryable": True}}
