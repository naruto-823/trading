from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.position_analysis import (
    get_latest_report,
    ingest_external_report,
    list_report_history,
)
from app.workers.scheduler import trigger_job_now

router = APIRouter()


class IngestBody(BaseModel):
    summary: str
    report_markdown: str
    alerts: list[str] | None = None
    model: str | None = None
    push: bool = True


@router.get("/position-analysis/latest")
def get_latest(db: Session = Depends(get_db)):
    return {"data": get_latest_report(db), "error": None}


@router.get("/position-analysis/history")
def get_history(
    limit: int = Query(24, ge=1, le=200, description="返回最近 N 条"),
    db: Session = Depends(get_db),
):
    return {"data": list_report_history(db, limit=limit), "error": None}


@router.post("/position-analysis/run-now")
async def run_now():
    """立即触发一次体检(复用 scheduler 的 trigger_job_now)。"""
    ok = await trigger_job_now("hourly-position-analysis")
    return {"data": {"triggered": ok}, "error": None if ok else "job 未注册(可能已禁用)"}


@router.post("/position-analysis/ingest")
def ingest(body: IngestBody, db: Session = Depends(get_db)):
    """外部(Claude Code headless)生成的报告落库 + Bark 推送。"""
    data = ingest_external_report(
        db,
        summary=body.summary,
        report_markdown=body.report_markdown,
        alerts=body.alerts,
        model=body.model,
        push=body.push,
    )
    return {"data": data, "error": None}
