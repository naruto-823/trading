from fastapi import APIRouter, HTTPException

from app.services.news_sources import available_sources
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
    """查看新闻源 fallback 链：哪些已配置、按 tier 排序"""
    return {"data": available_sources(), "error": None}
