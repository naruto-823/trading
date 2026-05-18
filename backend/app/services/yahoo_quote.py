"""第三方实时报价兜底

长桥的 L1 行情只覆盖到正常盘后（16:00-20:00 ET），20:00 ET 之后的
"扩展时段交易"（夜盘 / Overnight）拿不到。本模块通过 Nasdaq 官方接口
获取美股盘前/盘中/盘后/夜盘的最新成交价，作为长桥数据的补充。

数据源：
- Nasdaq Quote API: https://api.nasdaq.com/api/quote/{symbol}/info
- Nasdaq 扩展时段: https://api.nasdaq.com/api/quote/{symbol}/extended-trading

注：保留模块名为 yahoo_quote 是为了向后兼容，实际数据源是 Nasdaq。
"""

from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger(__name__)

NASDAQ_INFO_URL = "https://api.nasdaq.com/api/quote/{symbol}/info"
NASDAQ_EXT_URL = "https://api.nasdaq.com/api/quote/{symbol}/extended-trading"
DEFAULT_TIMEOUT = 5.0
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}


def _to_nasdaq_symbol(symbol: str) -> str | None:
    """把内部 symbol（如 MSFT.US）转成 Nasdaq 接口的 symbol 格式。
    目前仅支持美股；其他市场返回 None 表示不走 Nasdaq。
    """
    if not symbol:
        return None
    parts = symbol.rsplit(".", 1)
    if len(parts) != 2:
        return None
    base, market = parts
    if market.upper() == "US":
        return base
    return None


def _parse_money(text: str | None) -> float:
    """把 '$425.8539 -3.3961 (-0.79%)' 这样的字符串解析出第一个数字"""
    if not text:
        return 0.0
    # 匹配第一个 $ 后面的数字（含小数）
    match = re.search(r"\$([0-9]+(?:\.[0-9]+)?)", text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return 0.0
    # 没有 $ 时尝试解析纯数字
    match = re.search(r"-?[0-9]+(?:\.[0-9]+)?", text)
    if match:
        try:
            return float(match.group(0))
        except ValueError:
            return 0.0
    return 0.0


def _safe_float(val) -> float:
    if val is None:
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _fetch_nasdaq_info(client: httpx.Client, symbol: str) -> tuple[dict | None, str]:
    """获取常规盘最新价 + 上一交易日收盘价。

    Nasdaq /info 接口的 assetclass 必须正确（stocks vs etf vs index），不匹配时
    返回 200 但 data 为 null/空。优先按 stocks 试一次，data 为空再回退 etf。
    返回 (info_dict, asset_class_used) 供后续 /extended-trading 复用同一类。
    """
    for asset in ("stocks", "etf"):
        try:
            resp = client.get(
                NASDAQ_INFO_URL.format(symbol=symbol),
                params={"assetclass": asset},
            )
            resp.raise_for_status()
            data = resp.json()
            d = (data.get("data") or {})
            if d.get("symbol"):  # 该 asset class 匹配
                return data, asset
        except Exception as exc:
            logger.warning("Nasdaq info failed for %s (%s): %s", symbol, asset, exc)
    return None, "stocks"


def _fetch_nasdaq_extended(client: httpx.Client, symbol: str, market_type: str, asset: str = "stocks") -> float:
    """获取扩展时段（pre / post）的最新价"""
    try:
        resp = client.get(
            NASDAQ_EXT_URL.format(symbol=symbol),
            params={"assetclass": asset, "markettype": market_type},
        )
        resp.raise_for_status()
        data = resp.json()
        rows = (data.get("data") or {}).get("infoTable", {}).get("rows") or []
        if not rows:
            return 0.0
        consolidated = rows[0].get("consolidated", "")
        return _parse_money(consolidated)
    except Exception as exc:
        logger.warning("Nasdaq %s extended failed for %s: %s", market_type, symbol, exc)
        return 0.0


def fetch_yahoo_quote(symbol: str, client: httpx.Client | None = None, with_extended: bool = False) -> dict | None:
    """查询单只美股的最新报价（含盘后）。失败返回 None。

    Args:
        symbol: 内部 symbol，如 'MSFT.US'
        client: 复用的 httpx.Client（可选）。批量调用时传入复用连接，提升性能。
        with_extended: 是否额外调用扩展时段接口拿 pre/post 详细价。
                       仅在单只查询时建议开启；批量同步时建议关闭以保证速度。

    返回字段：
    - regular_market_price: 常规盘最新价
    - post_market_price: 盘后/夜盘最新价（如果有）
    - pre_market_price: 盘前最新价（如果有）
    - previous_close: 上一交易日收盘价
    """
    nasdaq_symbol = _to_nasdaq_symbol(symbol)
    if not nasdaq_symbol:
        return None

    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=DEFAULT_TIMEOUT, headers=DEFAULT_HEADERS)

    try:
        info, asset_used = _fetch_nasdaq_info(client, nasdaq_symbol)
        if not info:
            return None

        primary = (info.get("data") or {}).get("primaryData") or {}
        secondary = (info.get("data") or {}).get("secondaryData") or {}

        regular_price = _parse_money(primary.get("lastSalePrice"))
        net_change = _safe_float(primary.get("netChange"))
        previous_close = regular_price - net_change if regular_price > 0 else 0.0

        # secondary 在盘后/夜盘时段会包含最新成交价
        # 字段是 lastSalePrice，跟 primary 同名
        post_price = _parse_money(secondary.get("lastSalePrice")) if secondary else 0.0

        ext_post = 0.0
        ext_pre = 0.0
        if with_extended:
            ext_post = _fetch_nasdaq_extended(client, nasdaq_symbol, "post", asset_used)
            ext_pre = _fetch_nasdaq_extended(client, nasdaq_symbol, "pre", asset_used)

        return {
            "symbol": symbol,
            "regular_market_price": regular_price,
            "post_market_price": ext_post or post_price,
            "pre_market_price": ext_pre,
            "previous_close": previous_close,
            "market_state": "CLOSED",
            "currency": "USD",
        }
    except Exception as exc:
        logger.warning("Nasdaq fetch failed for %s: %s", symbol, exc)
        return None
    finally:
        if own_client:
            client.close()


def fetch_yahoo_quotes(symbols: list[str], with_extended: bool = False) -> dict[str, dict]:
    """批量查询美股报价。用线程池并发请求，速度优先。

    Args:
        symbols: 内部 symbol 列表，如 ['MSFT.US', 'GOOG.US']
        with_extended: 是否额外查扩展时段。批量场景默认关闭。
    """
    results: dict[str, dict] = {}
    if not symbols:
        return results

    # 过滤出能走 Nasdaq 接口的美股
    valid_symbols = [s for s in symbols if _to_nasdaq_symbol(s)]
    if not valid_symbols:
        return results

    # 并发请求：每个 symbol 独立的 client，并行发起
    # 用线程池而非 asyncio，避免和 FastAPI 主事件循环冲突
    from concurrent.futures import ThreadPoolExecutor, as_completed

    max_workers = min(len(valid_symbols), 10)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_symbol = {
            executor.submit(fetch_yahoo_quote, sym, None, with_extended): sym
            for sym in valid_symbols
        }
        for future in as_completed(future_to_symbol):
            sym = future_to_symbol[future]
            try:
                data = future.result()
                if data:
                    results[sym] = data
            except Exception as exc:
                logger.warning("Concurrent fetch failed for %s: %s", sym, exc)

    return results


def pick_latest_price(quote_data: dict) -> tuple[float, str]:
    """从报价数据里选出最新价 + 价格时段。
    返回 (price, session)，session 取值：pre / regular / post / closed。
    
    优先级：post（夜盘最新）> pre（盘前最新）> regular > 0
    """
    regular = quote_data.get("regular_market_price", 0.0)
    post = quote_data.get("post_market_price", 0.0)
    pre = quote_data.get("pre_market_price", 0.0)

    # 美股盘后或夜盘有数据 → 优先用 post（这是最新的）
    if post > 0 and post != regular:
        return post, "post"
    # 盘前有数据
    if pre > 0 and pre != regular:
        return pre, "pre"
    if regular > 0:
        return regular, "regular"
    return 0.0, "closed"
