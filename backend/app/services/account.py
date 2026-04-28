from sqlalchemy.orm import Session

from app.models.account import AccountSnapshot
from app.schemas.account import AccountSnapshotResponse


def get_latest_account(db: Session) -> AccountSnapshotResponse | None:
    snapshot = db.query(AccountSnapshot).order_by(AccountSnapshot.synced_at.desc()).first()
    if not snapshot:
        return None
    return AccountSnapshotResponse.model_validate(snapshot)
