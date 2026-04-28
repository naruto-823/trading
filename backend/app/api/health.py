from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health_check():
    return {"data": {"status": "ok"}, "error": None}
