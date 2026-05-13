"""实时报价服务

针对美股，按所处时段（盘前 / 盘中 / 盘后 / 已收盘）选择合适的当前价：
- 盘前 (04:00-09:30 ET) → pre_market_quote.last_done
- 盘中 (09:30-16:00 ET) → last_done（常规盘最新价）
- 盘后 (16:00-20:00 ET) → post_market_quote.last_done
- 已收盘 → last_done（常规盘收盘价）

非美股标的（.HK / .SG 等）不区分盘前盘后，直接使用 last_done。
"""

from datetime import datetime, time, timezone, timedelta

from app.config import settings
from app.schemas.quote import QuoteResponse

# 美东时间相对 UTC 的偏移：标准时 -5、夏令时 -4。
# 美国夏令时：3 月第二个周日 02:00 → 11 月第一个周日 02:00。
# 这里给一个轻量实现，避免引入 pytz/zoneinfo 兼容性问题。
def _et_now(now_utc: datetime | None = None) -> datetime:
    """把 UTC 时间转成美东时间（自动处理夏令时）"""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    year = now_utc.year
    # 3 月第二个周日
    march_first = datetime(year, 3, 1, tzinfo=timezone.utc)
    days_to_sunday = (6 - march_first.weekday()) % 7
    dst_start = march_first + timedelta(days=days_to_sunday + 7)
    dst_start = dst_start.replace(hour=7)  # 02:00 ET = 07:00 UTC（EST→EDT 切换瞬间）

    # 11 月第一个周日
    nov_first = datetime(year, 11, 1, tzinfo=timezone.utc)
    days_to_sunday = (6 - nov_first.weekday()) % 7
    dst_end = nov_first + timedelta(days=days_to_sunday)
    dst_end = dst_end.replace(hour=6)  # 02:00 EDT = 06:00 UTC（EDT→EST 切换瞬间）

    is_dst = dst_start <= now_utc < dst_end
    offset_hours = -4 if is_dst else -5
    return now_utc + timedelta(hours=offset_hours)

def _us_session(now_utc: datetime | None = None) -> str:
    """返回美股当前所处时段：pre / regular / post / overnight / closed"""
    et = _et_now(now_utc)
    # 周末直接视为收盘
    if et.weekday() >= 5:
        return "closed"
    t = et.time()
    if time(4, 0) <= t < time(9, 30):
        return "pre"
    if time(9, 30) <= t < time(16, 0):
        return "regular"
    if time(16, 0) <= t < time(20, 0):
        return "post"
    if time(20, 0) <= t <= time(23, 59):
        return "overnight"
    return "closed"

def _is_us_symbol(symbol: str) -> bool:
    return symbol.rsplit(".", 1)[-1].upper() == "US"

def _safe_float(obj, attr: str) -> float:
    """安全读取 SDK 对象的浮点字段"""
    if obj is None or not hasattr(obj, attr):
        return 0.0
    val = getattr(obj, attr)
    if val is None:
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0

def _build_quote_response(quote, now_utc: datetime) -> QuoteResponse:
    """把 SDK quote 对象转成 QuoteResponse，按时段选取 current_price"""
    symbol = str(quote.symbol) if hasattr(quote, "symbol") else ""
    name = str(quote.symbol_name) if hasattr(quote, "symbol_name") else ""

    last_done = _safe_float(quote, "last_done")
    prev_close = _safe_float(quote, "prev_close")
    open_ = _safe_float(quote, "open")
    high = _safe_float(quote, "high")
    low = _safe_float(quote, "low")
    volume = int(getattr(quote, "volume", 0) or 0)
    turnover = _safe_float(quote, "turnover")

    pre_q = getattr(quote, "pre_market_quote", None)
    post_q = getattr(quote, "post_market_quote", None)
    pre_price = _safe_float(pre_q, "last_done")
    post_price = _safe_float(post_q, "last_done")

    pre_change = pre_price - prev_close if pre_price and prev_close else 0.0
    pre_change_ratio = (pre_change / prev_close * 100) if pre_price and prev_close else 0.0
    post_change = post_price - prev_close if post_price and prev_close else 0.0
    post_change_ratio = (post_change / prev_close * 100) if post_price and prev_close else 0.0

    # 决定当前价与时段
    if _is_us_symbol(symbol):
        session = _us_session(now_utc)
        if session == "pre" and pre_price > 0:
            current_price = pre_price
        elif session == "post" and post_price > 0:
            current_price = post_price
        elif session == "regular" and last_done > 0:
            current_price = last_done
        else:
            # closed 时段：优先看是否还有最新的盘后/盘前残留价格作为参考，否则用 last_done
            current_price = last_done or post_price or pre_price
    else:
        session = "regular"  # 非美股不细分
        current_price = last_done

    change = current_price - prev_close if current_price and prev_close else 0.0
    change_ratio = (change / prev_close * 100) if current_price and prev_close else 0.0

    return QuoteResponse(
        symbol=symbol,
        name=name,
        current_price=current_price,
        prev_close=prev_close,
        open=open_,
        high=high,
        low=low,
        last_done=last_done,
        volume=volume,
        turnover=turnover,
        pre_market_price=pre_price,
        pre_market_change=round(pre_change, 4),
        pre_market_change_ratio=round(pre_change_ratio, 4),
        post_market_price=post_price,
        post_market_change=round(post_change, 4),
        post_market_change_ratio=round(post_change_ratio, 4),
        trading_session=session,
        change=round(change, 4),
        change_ratio=round(change_ratio, 4),
        timestamp=str(quote.timestamp) if hasattr(quote, "timestamp") else "",
    )

def get_realtime_quotes(symbols: list[str]) -> list[QuoteResponse]:
    if not settings.validate_longport():
        return _get_mock_quotes(symbols)

    from app.longbridge.client import get_quote_context
    ctx = get_quote_context()
    quotes = ctx.quote(symbols)
    now_utc = datetime.now(timezone.utc)

    responses = [_build_quote_response(q, now_utc) for q in quotes]

    # 补充：夜盘/收盘时段用 Nasdaq 更新美股价格
    us_symbols = [s for s in symbols if _is_us_symbol(s)]
    session = _us_session(now_utc)
    if us_symbols and session in ("overnight", "closed"):
        from app.services.yahoo_quote import fetch_yahoo_quotes, pick_latest_price
        nasdaq_quotes = fetch_yahoo_quotes(us_symbols, with_extended=False)

        for resp in responses:
            if resp.symbol in nasdaq_quotes:
                nq = nasdaq_quotes[resp.symbol]
                latest_price, price_session = pick_latest_price(nq)
                if latest_price > 0 and price_session == "post":
                    resp.post_market_price = latest_price
                    resp.current_price = latest_price
                    if resp.prev_close > 0:
                        resp.post_market_change = round(latest_price - resp.prev_close, 4)
                        resp.post_market_change_ratio = round((latest_price - resp.prev_close) / resp.prev_close * 100, 4)
                        resp.change = resp.post_market_change
                        resp.change_ratio = resp.post_market_change_ratio

    return responses

def _get_mock_quotes(symbols: list[str]) -> list[QuoteResponse]:
    from app.longbridge.mock_data import MOCK_QUOTES

    now_utc = datetime.now(timezone.utc)
    results = []
    for symbol in symbols:
        quote_data = MOCK_QUOTES.get(symbol)
        if not quote_data:
            results.append(QuoteResponse(symbol=symbol, name=f"未知标的 ({symbol})"))
            continue

        last_done = float(quote_data["last_done"])
        prev_close = float(quote_data["prev_close"])
        pre_price = float(quote_data.get("pre_market_price") or 0.0)
        post_price = float(quote_data.get("post_market_price") or 0.0)

        if _is_us_symbol(symbol):
            session = _us_session(now_utc)
            if session == "pre" and pre_price > 0:
                current_price = pre_price
            elif session == "post" and post_price > 0:
                current_price = post_price
            elif session == "regular":
                current_price = last_done
            else:
                current_price = last_done or post_price or pre_price
        else:
            session = "regular"
            current_price = last_done

        pre_change = pre_price - prev_close if pre_price and prev_close else 0.0
        pre_change_ratio = (pre_change / prev_close * 100) if pre_price and prev_close else 0.0
        post_change = post_price - prev_close if post_price and prev_close else 0.0
        post_change_ratio = (post_change / prev_close * 100) if post_price and prev_close else 0.0
        change = current_price - prev_close if current_price and prev_close else 0.0
        change_ratio = (change / prev_close * 100) if current_price and prev_close else 0.0

        results.append(
            QuoteResponse(
                symbol=quote_data["symbol"],
                name=quote_data["symbol_name"],
                current_price=current_price,
                prev_close=prev_close,
                open=float(quote_data["open"]),
                high=float(quote_data["high"]),
                low=float(quote_data["low"]),
                last_done=last_done,
                volume=int(quote_data["volume"]),
                turnover=float(quote_data["turnover"]),
                pre_market_price=pre_price,
                pre_market_change=round(pre_change, 4),
                pre_market_change_ratio=round(pre_change_ratio, 4),
                post_market_price=post_price,
                post_market_change=round(post_change, 4),
                post_market_change_ratio=round(post_change_ratio, 4),
                trading_session=session,
                change=round(change, 4),
                change_ratio=round(change_ratio, 4),
                timestamp=quote_data["timestamp"],
            )
        )

    return results