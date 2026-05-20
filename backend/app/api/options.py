"""期权链 / 期权报价 API"""

from fastapi import APIRouter, Query

from app.longbridge import options as lb_options

router = APIRouter()


def _err(code: str, msg: str, retryable: bool = False) -> dict:
    return {"data": None, "error": {"code": code, "message": msg, "retryable": retryable}}


@router.get("/options/expiries")
def get_expiries(symbol: str = Query(..., description="标的代码，如 MSFT.US")):
    """返回该标的所有可用到期日（ISO 日期，按升序）"""
    try:
        return {"data": {"symbol": symbol.upper(), "expiries": lb_options.get_expiries(symbol)}, "error": None}
    except Exception as exc:
        return _err("OPTION_EXPIRY_ERROR", str(exc), retryable=True)


@router.get("/options/chain")
def get_chain(
    symbol: str = Query(..., description="标的代码，如 MSFT.US"),
    expiry: str = Query(..., description="ISO 日期，如 2026-06-18"),
    around: float | None = Query(
        None, description="可选：只返回 strike 在 around 上下 N 档范围内的合约（节省返回体积）"
    ),
    n: int = Query(15, ge=1, le=50, description="around 参数下，上下各取 N 档"),
):
    try:
        rows = lb_options.get_chain(symbol, expiry)
        if around is not None and rows:
            # 取最接近 around 的索引，左右各 n 档
            idx = min(range(len(rows)), key=lambda i: abs(rows[i].strike - around))
            lo, hi = max(0, idx - n), min(len(rows), idx + n + 1)
            rows = rows[lo:hi]
        return {
            "data": {
                "symbol": symbol.upper(),
                "expiry": expiry,
                "strikes": [r.model_dump() for r in rows],
            },
            "error": None,
        }
    except Exception as exc:
        return _err("OPTION_CHAIN_ERROR", str(exc), retryable=True)


@router.get("/options/quote")
def get_option_quote(symbols: str = Query(..., description="逗号分隔的期权 symbol，最多 50 个")):
    syms = [s.strip() for s in symbols.split(",") if s.strip()][:50]
    if not syms:
        return _err("INVALID_PARAMS", "symbols 不能为空")
    try:
        rows = lb_options.get_option_quotes(syms)
        return {"data": [r.model_dump() for r in rows], "error": None}
    except Exception as exc:
        return _err("OPTION_QUOTE_ERROR", str(exc), retryable=True)
