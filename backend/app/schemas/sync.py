from datetime import datetime

from pydantic import BaseModel


class SyncResult(BaseModel):
    kind: str
    status: str
    rows_written: int = 0
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class SyncLogResponse(BaseModel):
    id: int
    kind: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    error: str | None
    rows_written: int

    model_config = {"from_attributes": True}
