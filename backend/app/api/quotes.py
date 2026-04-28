from fastapi import APIRouter, Query

from app.services.quote import get_realtime_quotes

router = APIRouter()


@router.get("/quote")
def get_quote(symbols: str = Query(..., description="逗号分隔的标的代码，如 AAPL.US,700.HK")):
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        return {"data": [], "error": {"code": "INVALID_PARAMS", "message": "symbols 不能为空", "retryable": False}}
    try:
        quotes = get_realtime_quotes(symbol_list)
        return {"data": [q.model_dump() for q in quotes], "error": None}
    except Exception as exc:
        return {"data": None, "error": {"code": "QUOTE_ERROR", "message": str(exc), "retryable": True}}
