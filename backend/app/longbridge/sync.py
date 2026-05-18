"""从长桥 OpenAPI 同步数据到 SQLite（支持 mock 模式）"""

import json
import logging
import traceback
import urllib.request
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.models.account import AccountSnapshot
from app.models.execution import Execution
from app.models.order import Order
from app.models.position import Position
from app.models.sync_log import SyncLog

logger = logging.getLogger(__name__)

_cached_fx_rates: dict[str, float] | None = None

def _get_fx_rates() -> dict[str, float]:
    """获取多币种汇率（USD-base），带缓存。
    返回 {"USD_HKD": 7.83, "USD_CNY": 7.24, "HKD_CNY": 0.92, ...}"""
    global _cached_fx_rates
    if _cached_fx_rates is not None:
        return _cached_fx_rates

    fallback = {"USD_HKD": 7.8315, "USD_CNY": 7.24, "HKD_CNY": 0.92, "CNY_HKD": 1.08}
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        req = urllib.request.Request(url, headers={"User-Agent": "trading-app/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if data.get("result") == "success" and "rates" in data:
                rates = data["rates"]
                usd_hkd = float(rates.get("HKD", fallback["USD_HKD"]))
                usd_cny = float(rates.get("CNY", fallback["USD_CNY"]))
                result = {
                    "USD_HKD": usd_hkd,
                    "USD_CNY": usd_cny,
                    "HKD_CNY": usd_cny / usd_hkd if usd_hkd else fallback["HKD_CNY"],
                    "CNY_HKD": usd_hkd / usd_cny if usd_cny else fallback["CNY_HKD"],
                }
                logger.info("FX rates from API: USD/HKD=%.4f USD/CNY=%.4f HKD/CNY=%.4f",
                            usd_hkd, usd_cny, result["HKD_CNY"])
                _cached_fx_rates = result
                return result
    except Exception as exc:
        logger.warning("Failed to fetch FX rates: %s, using fallback %s", exc, fallback)

    _cached_fx_rates = fallback
    return fallback


def _get_usd_hkd_rate() -> float:
    """向后兼容：取 USD/HKD"""
    return _get_fx_rates()["USD_HKD"]


def _reset_rate_cache() -> None:
    """重置汇率缓存，在每次完整同步时调用"""
    global _cached_fx_rates
    _cached_fx_rates = None

def _use_mock() -> bool:
    return not settings.validate_longport()

def _use_mock() -> bool:
    return not settings.validate_longport()


def _clean_enum(value: str) -> str:
    """清理 SDK 枚举字符串，如 'OrderSide.Buy' → 'Buy'"""
    if "." in value:
        return value.rsplit(".", 1)[-1]
    return value

def _is_option_symbol(symbol: str) -> bool:
    """判断是否为期权标的（如 MSFT260515P350000.US）"""
    parts = symbol.rsplit(".", 1)
    if len(parts) != 2:
        return False
    ticker = parts[0]
    # 期权标的通常包含日期+P/C+行权价的模式，长度较长
    return len(ticker) > 10 and any(c in ticker for c in "PC") and any(c.isdigit() for c in ticker[-6:])

def _is_quote_from_today(quote, now_utc: datetime) -> bool:
    """判断 quote 数据是否属于今天的交易日。

    港股：timestamp 是 UTC+8，比较北京时间日期
    美股正股：正式盘 + 盘前盘后都有连续报价
    美股期权：只有正式盘有交易，盘前盘后无交易

    对于美股正股：如果有盘前报价，认为今天有交易
    对于美股期权：需要正式盘（UTC 13:30-20:00）的 timestamp 才算今天
    """
    if not hasattr(quote, "timestamp") or quote.timestamp is None:
        return False

    try:
        from datetime import timedelta

        ts = quote.timestamp
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))

        sym = str(quote.symbol) if hasattr(quote, "symbol") else ""
        parts = sym.rsplit(".", 1)
        market = parts[-1] if len(parts) > 1 else ""

        if market == "HK":
            # 港股 timestamp 已经是 UTC+8
            today_hk = (now_utc + timedelta(hours=8)).date()
            ts_date = ts.date()
            return ts_date == today_hk
        else:
            # 美股 timestamp 是 UTC
            # 美股正式盘：UTC 13:30 - 20:00（北京时间 21:30 - 04:00+1）
            # 美股盘后到 UTC 00:00（北京 08:00+1）
            # 美股盘前从 UTC 08:00（北京 16:00）
            #
            # 对于美股正股和期权，判断正式盘 timestamp：
            # 如果 timestamp 的 UTC 日期和 now 的 UTC 日期相同，
            # 且 timestamp 的 UTC 小时 >= 13（开盘13:30），说明今天正式盘有交易
            ts_date = ts.date()
            today_utc = now_utc.date()

            if ts_date == today_utc and ts.hour >= 13:
                # 今天正式盘有交易
                return True

            # 如果正式盘还没开（比如现在是北京时间白天，美股还没开盘），
            # 此时 timestamp 是昨天收盘的时间，不算今天
            return False
    except Exception:
        return False


def _compute_sold_today_day_pnl_by_market(
    db: Session, usd_to_hkd: float
) -> tuple[dict[str, float], float]:
    """今日已平仓股票对"今日盈亏"的贡献，按市场拆分（原币）+ 折合 HKD 总额。

    定义口径：账户的"今日盈亏" = sum(持仓的 prev_close→current 移动)
                              + sum(今日已平仓股票的 prev_close→成交价 移动)
    第二项是 Position 表统计不到的：股票卖完后从 Position 消失，
    它今天从昨收到卖价那段的移动就丢了。

    只处理股票，期权由 sync_positions 的合约级 day_pnl 逻辑覆盖。

    返回：( {"HK": hkd_amount, "US": usd_amount, ...}, total_hkd )
    """
    today_start_utc = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_execs = db.query(Execution).filter(Execution.trade_done_at >= today_start_utc).all()
    stock_sells = [
        e for e in today_execs
        if (e.side or "").upper().startswith("S") and not _is_option_symbol(e.symbol)
    ]
    if not stock_sells:
        return {}, 0.0

    # 拉每个标的的 prev_close（昨日收盘）。当前仍持有的标的可直接复用 Position.prev_close。
    prev_close_map: dict[str, float] = {}
    held = {p.symbol: float(p.prev_close) for p in db.query(Position).all() if p.prev_close}
    need_quote = [e.symbol for e in stock_sells if e.symbol not in held]
    for sym, pc in held.items():
        prev_close_map[sym] = pc

    if need_quote:
        try:
            from app.longbridge.client import get_quote_context
            quote_ctx = get_quote_context()
            for q in quote_ctx.quote(list(set(need_quote))):
                sym = str(q.symbol)
                if hasattr(q, "prev_close") and q.prev_close is not None:
                    prev_close_map[sym] = float(q.prev_close)
        except Exception as exc:
            logger.warning("quote fetch for sold-today symbols failed: %s", exc)

    by_market: dict[str, float] = {}
    total_hkd = 0.0
    for exe in stock_sells:
        prev_close = prev_close_map.get(exe.symbol, 0.0)
        if prev_close <= 0:
            continue
        contribution_native = (float(exe.price) - prev_close) * float(exe.quantity)
        by_market[exe.market] = by_market.get(exe.market, 0.0) + contribution_native
        if (exe.currency or "").upper() == "USD":
            total_hkd += contribution_native * usd_to_hkd
        else:
            total_hkd += contribution_native
    return by_market, total_hkd


def _start_sync_log(db: Session, kind: str) -> SyncLog:
    log = SyncLog(kind=kind, started_at=datetime.utcnow(), status="running")
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def _finish_sync_log(db: Session, log: SyncLog, status: str, rows: int = 0, error: str | None = None) -> None:
    log.finished_at = datetime.utcnow()
    log.status = status
    log.rows_written = rows
    log.error = error
    db.commit()


def sync_account(db: Session) -> SyncLog:
    log = _start_sync_log(db, "account")
    try:
        if _use_mock():
            from app.longbridge.mock_data import MOCK_ACCOUNT_BALANCES
            balances = MOCK_ACCOUNT_BALANCES
        else:
            from app.longbridge.client import get_trade_context
            ctx = get_trade_context()
            balances = ctx.account_balance()

        rows = 0
        for balance in balances:
            if isinstance(balance, dict):
                snapshot = AccountSnapshot(
                    synced_at=datetime.utcnow(),
                    currency=balance.get("currency", "HKD"),
                    total_cash=balance.get("total_cash", 0.0),
                    net_assets=balance.get("net_assets", 0.0),
                    market_value=balance.get("market_value", 0.0),
                    total_pnl=balance.get("unrealized_pl", 0.0),
                    day_pnl=0.0,
                    raw_json=json.dumps(balance, ensure_ascii=False),
                )
            else:
                # 真实 SDK: net_assets 和 total_cash 已经是长桥按实时汇率换算的 HKD 值
                net_assets_val = float(balance.net_assets) if hasattr(balance, "net_assets") else 0.0
                total_cash_val = float(balance.total_cash) if hasattr(balance, "total_cash") else 0.0
                market_value_val = net_assets_val - total_cash_val

                # 获取多币种汇率
                fx_rates = _get_fx_rates()
                usd_to_hkd = fx_rates["USD_HKD"]

                # 从持仓表汇总计算总浮动盈亏和当日盈亏（按货币分组，USD 转 HKD）
                from sqlalchemy import func
                currency_pnl = (
                    db.query(
                        Position.currency,
                        func.sum(Position.unrealized_pnl).label("pnl"),
                        func.sum(Position.day_pnl).label("day_pnl"),
                    )
                    .group_by(Position.currency)
                    .all()
                )
                total_pnl_val = 0.0
                unrealized_day_pnl_hkd = 0.0
                for currency, pnl, day_pnl in currency_pnl:
                    pnl_float = float(pnl or 0)
                    day_pnl_float = float(day_pnl or 0)
                    if currency == "USD":
                        total_pnl_val += pnl_float * usd_to_hkd
                        unrealized_day_pnl_hkd += day_pnl_float * usd_to_hkd
                    else:
                        total_pnl_val += pnl_float
                        unrealized_day_pnl_hkd += day_pnl_float

                # 今日已平仓股票对当日盈亏的贡献（昨收 → 卖出价）。
                # Position 表只统计当前仍持有的标的的 prev_close→current 移动，
                # 今天卖掉的股票从 Position 消失后这段日内移动就丢了，需要在账户层补齐。
                realized_by_market, realized_day_pnl_hkd = _compute_sold_today_day_pnl_by_market(
                    db, usd_to_hkd
                )
                day_pnl_val = unrealized_day_pnl_hkd + realized_day_pnl_hkd

                # 融资 / 保证金信息（长桥已按实时汇率换算成账户主币种 HKD）
                def _safe_dec(obj, attr: str) -> float:
                    v = getattr(obj, attr, None)
                    if v is None:
                        return 0.0
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return 0.0

                cash_infos_serialized: list[dict] = []
                # 真实融资欠款（HKD）= 把所有币种的负 available 折成 HKD
                # 这才是长桥 app "融资欠款" 字段的口径。
                outstanding_debt_hkd = 0.0
                for ci in getattr(balance, "cash_infos", []) or []:
                    cur = str(getattr(ci, "currency", ""))
                    available = _safe_dec(ci, "available_cash")
                    cash_infos_serialized.append({
                        "currency": cur,
                        "available": available,
                        "withdraw": _safe_dec(ci, "withdraw_cash"),
                        "frozen": _safe_dec(ci, "frozen_cash"),
                        "settling": _safe_dec(ci, "settling_cash"),
                    })
                    if available < 0:
                        if cur.upper() == "USD":
                            outstanding_debt_hkd += available * usd_to_hkd
                        else:
                            # HKD / 其他币种暂按 1:1 折算
                            outstanding_debt_hkd += available

                snapshot = AccountSnapshot(
                    synced_at=datetime.utcnow(),
                    currency=str(balance.currency) if hasattr(balance, "currency") else "HKD",
                    total_cash=total_cash_val,
                    net_assets=net_assets_val,
                    market_value=market_value_val,
                    total_pnl=float(total_pnl_val),
                    day_pnl=float(day_pnl_val),
                    realized_day_pnl=float(realized_day_pnl_hkd),
                    realized_day_pnl_by_market=json.dumps(realized_by_market, ensure_ascii=False),
                    max_finance_amount=_safe_dec(balance, "max_finance_amount"),
                    remaining_finance_amount=_safe_dec(balance, "remaining_finance_amount"),
                    outstanding_debt=float(outstanding_debt_hkd),
                    init_margin=_safe_dec(balance, "init_margin"),
                    maintenance_margin=_safe_dec(balance, "maintenance_margin"),
                    buy_power=_safe_dec(balance, "buy_power"),
                    margin_call=int(getattr(balance, "margin_call", 0) or 0),
                    risk_level=int(getattr(balance, "risk_level", 0) or 0),
                    cash_infos_json=json.dumps(cash_infos_serialized, ensure_ascii=False),
                    fx_rates_json=json.dumps(fx_rates, ensure_ascii=False),
                    raw_json=json.dumps(str(balance)),
                )
            db.add(snapshot)
            rows += 1

        db.commit()
        _finish_sync_log(db, log, "success", rows)
    except Exception as exc:
        db.rollback()
        _finish_sync_log(db, log, "error", error=f"{exc}\n{traceback.format_exc()}")
    return log


def sync_positions(db: Session) -> SyncLog:
    log = _start_sync_log(db, "positions")
    try:
        # 清空旧持仓（快照覆盖策略）
        db.query(Position).delete()

        rows = 0

        if _use_mock():
            from app.longbridge.mock_data import MOCK_POSITIONS
            for pos_data in MOCK_POSITIONS:
                symbol_raw = pos_data["symbol"]
                parts = symbol_raw.rsplit(".", 1)
                market = parts[1] if len(parts) > 1 else ""

                position = Position(
                    synced_at=datetime.utcnow(),
                    symbol=symbol_raw,
                    market=market,
                    name=pos_data.get("symbol_name", ""),
                    quantity=pos_data.get("quantity", 0),
                    available_qty=pos_data.get("available_quantity", 0),
                    cost_price=pos_data.get("cost_price", 0.0),
                    current_price=pos_data.get("current_price", 0.0),
                    market_value=pos_data.get("market_value", 0.0),
                    unrealized_pnl=pos_data.get("unrealized_pl", 0.0),
                    unrealized_pnl_ratio=pos_data.get("unrealized_pl_ratio", 0.0),
                    currency=pos_data.get("currency", "HKD"),
                    raw_json=json.dumps(pos_data, ensure_ascii=False),
                )
                db.add(position)
                rows += 1
        else:
            from app.longbridge.client import get_trade_context, get_quote_context
            ctx = get_trade_context()
            response = ctx.stock_positions()

            # 收集所有持仓标的，批量获取报价
            all_positions = []
            channels = response.channels if hasattr(response, "channels") else []
            for channel in channels:
                positions_list = channel.positions if hasattr(channel, "positions") else []
                all_positions.extend(positions_list)

            # 批量获取报价来补充 current_price 和 prev_close
            symbol_prices: dict[str, float] = {}
            symbol_prev_close: dict[str, float] = {}
            symbol_is_trading_today: dict[str, bool] = {}
            stock_symbols = [str(p.symbol) for p in all_positions if not _is_option_symbol(str(p.symbol))]
            option_symbols = [str(p.symbol) for p in all_positions if _is_option_symbol(str(p.symbol))]

            now_utc = datetime.utcnow()

            # 期权当日盈亏需要知道"今天是否新开仓/加仓"，
            # 因为长桥返回的 prev_close 是合约自身昨日收盘价，
            # 而用户昨天可能根本没持有这个合约（合约一直在市场上但用户今天才买）。
            # 通过查询 Execution 表的今日成交记录，得到每只期权今日的净成交量（带方向）。
            # 净成交量的方向规则：买入 (Buy/B) 为正，卖出 (Sell/S) 为负。
            from sqlalchemy import func as _sql_func
            today_start_utc = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            option_today_filled_qty: dict[str, int] = {}
            if option_symbols:
                today_executions = (
                    db.query(Execution.symbol, Execution.side, Execution.quantity)
                    .filter(Execution.symbol.in_(option_symbols))
                    .filter(Execution.trade_done_at >= today_start_utc)
                    .all()
                )
                for sym, side, qty_filled in today_executions:
                    side_upper = (side or "").upper()
                    signed_qty = qty_filled if side_upper.startswith("B") else -qty_filled
                    option_today_filled_qty[sym] = option_today_filled_qty.get(sym, 0) + signed_qty

            # us_session 期权段也要用，提到外层算一次（pure function 无副作用）
            from app.services.quote import _us_session
            us_session = _us_session(now_utc)

            if stock_symbols:
                try:
                    quote_ctx = get_quote_context()
                    quotes = quote_ctx.quote(stock_symbols)
                    for q in quotes:
                        sym = str(q.symbol)
                        regular_last_done = float(q.last_done) if hasattr(q, "last_done") else 0.0
                        prev_close_val = float(q.prev_close) if hasattr(q, "prev_close") else 0.0
                        is_today = _is_quote_from_today(q, now_utc)

                        current_price = regular_last_done
                        is_us = sym.rsplit(".", 1)[-1].upper() == "US"

                        # 当日盈亏基准（"上一交易日收盘价"参照）
                        is_us = sym.rsplit(".", 1)[-1].upper() == "US"
                        if is_us:
                            if us_session == "pre":
                                # 盘前：last_done = 昨日 regular 收盘
                                day_pnl_base = regular_last_done
                            elif us_session == "regular":
                                # 盘中：prev_close = 昨日 regular 收盘
                                day_pnl_base = prev_close_val
                            else:
                                # post / overnight / closed：last_done = 最近一次 regular 收盘
                                # 日内盈亏 = 现价（post/overnight 价）− regular 收盘 = 延伸时段移动
                                day_pnl_base = regular_last_done
                        else:
                            # 港股等：用 prev_close
                            day_pnl_base = prev_close_val

                        # 选当前价：post/overnight/closed 优先取 post-market（更接近现价），
                        # 后续 Nasdaq fallback 在夜盘时会用 live post-market 再覆盖一次
                        if is_us:
                            pre_q = getattr(q, "pre_market_quote", None)
                            post_q = getattr(q, "post_market_quote", None)
                            pre_last = float(pre_q.last_done) if pre_q is not None and hasattr(pre_q, "last_done") and pre_q.last_done is not None else 0.0
                            post_last = float(post_q.last_done) if post_q is not None and hasattr(post_q, "last_done") and post_q.last_done is not None else 0.0

                            if us_session == "pre" and pre_last > 0:
                                current_price = pre_last
                                is_today = True
                            elif us_session == "post" and post_last > 0:
                                current_price = post_last
                                is_today = True
                            elif us_session == "regular" and regular_last_done > 0:
                                current_price = regular_last_done
                                is_today = True
                            else:
                                # overnight / closed：用 post-market 最近一次成交
                                current_price = post_last or regular_last_done or pre_last

                        symbol_prices[sym] = current_price
                        symbol_prev_close[sym] = day_pnl_base
                        symbol_is_trading_today[sym] = is_today

                    # 夜盘/休市时段用 Nasdaq 兜底刷新美股 post-market 价。Longbridge 的
                    # post_market_quote 在 ET 20:00 之后会冻结（账户没开通真夜盘订阅），
                    # 而 HK 上午 ≈ 美股 post 16:00-20:00 ET 期间 Nasdaq 的 secondary 段是
                    # live 的，能持续推送 post-market 实时成交。
                    us_stocks = [s for s in stock_symbols if s.rsplit(".", 1)[-1].upper() == "US"]
                    if us_stocks and us_session in ("overnight", "closed"):
                        from app.services.yahoo_quote import fetch_yahoo_quotes, pick_latest_price
                        nasdaq_data = fetch_yahoo_quotes(us_stocks, with_extended=False)
                        for sym, nq in nasdaq_data.items():
                            latest, sess = pick_latest_price(nq)
                            if latest > 0 and sess == "post":
                                symbol_prices[sym] = latest
                                symbol_is_trading_today[sym] = True
                except Exception as exc:
                    logger.warning("Failed to fetch stock quotes: %s", exc)


            # 获取期权报价（失败时重置连接重试一次）
            if option_symbols:
                try:
                    quote_ctx = get_quote_context()
                    option_quotes = quote_ctx.option_quote(option_symbols)
                    for q in option_quotes:
                        sym = str(q.symbol)
                        symbol_prices[sym] = float(q.last_done) if hasattr(q, "last_done") else 0.0
                        symbol_prev_close[sym] = float(q.prev_close) if hasattr(q, "prev_close") else 0.0
                        symbol_is_trading_today[sym] = _is_quote_from_today(q, now_utc)
                except Exception as exc:
                    # 可能是缓存的旧连接没有期权权限，重置后重试
                    logger.warning("Option quote failed, retrying with fresh context: %s", exc)
                    try:
                        from app.longbridge.client import reset_quote_context
                        reset_quote_context()
                        quote_ctx = get_quote_context()
                        option_quotes = quote_ctx.option_quote(option_symbols)
                        for q in option_quotes:
                            sym = str(q.symbol)
                            symbol_prices[sym] = float(q.last_done) if hasattr(q, "last_done") else 0.0
                            symbol_prev_close[sym] = float(q.prev_close) if hasattr(q, "prev_close") else 0.0
                            symbol_is_trading_today[sym] = _is_quote_from_today(q, now_utc)
                    except Exception as exc2:
                        logger.warning("Option quote retry also failed: %s", exc2)

            for pos in all_positions:
                symbol_raw = str(pos.symbol) if hasattr(pos, "symbol") else ""
                parts = symbol_raw.rsplit(".", 1)
                market = parts[1] if len(parts) > 1 else ""

                qty = int(pos.quantity) if hasattr(pos, "quantity") else 0
                cost = float(pos.cost_price) if hasattr(pos, "cost_price") else 0.0
                current = symbol_prices.get(symbol_raw, 0.0)
                prev_close = symbol_prev_close.get(symbol_raw, 0.0)
                is_option = _is_option_symbol(symbol_raw)

                if is_option:
                    multiplier = 100
                    if current:
                        mkt_val = abs(qty) * current * multiplier
                        cost_val = abs(qty) * cost * multiplier
                        if qty < 0:
                            pnl = (cost - current) * abs(qty) * multiplier
                        else:
                            pnl = (current - cost) * abs(qty) * multiplier
                    else:
                        cost_val = abs(qty) * cost * multiplier
                        if qty < 0:
                            mkt_val = 0.0
                            pnl = cost_val
                        else:
                            mkt_val = 0.0
                            pnl = -cost_val
                    pnl_ratio = pnl / cost_val if cost_val else 0.0

                    # 期权当日盈亏：只在美股 regular 时段算，其他时段（pre/post/overnight/closed）
                    # 期权流动性差、报价噪声大，强算会让两列看着几乎相等且数字飘忽。直接归零更清楚。
                    today_signed = option_today_filled_qty.get(symbol_raw, 0)
                    is_us_option = market.upper() == "US"

                    if is_us_option and us_session != "regular":
                        # 美股期权非正式盘内：当日盈亏直接 0
                        day_pnl_val = 0.0
                        day_pnl_ratio_val = 0.0
                    elif current and qty != 0 and today_signed == qty:
                        # 情况 1：今日全新开/翻仓
                        day_pnl_val = pnl
                        day_pnl_ratio_val = pnl_ratio
                    elif current and prev_close and today_signed == 0:
                        # 情况 2：今日没成交 → 昨天就持有，用 prev_close 推导日内移动
                        if qty < 0:
                            day_pnl_val = (prev_close - current) * abs(qty) * multiplier
                        else:
                            day_pnl_val = (current - prev_close) * abs(qty) * multiplier
                        prev_val = abs(qty) * prev_close * multiplier
                        day_pnl_ratio_val = day_pnl_val / prev_val if prev_val else 0.0
                    elif current and qty != 0:
                        # 情况 4：今日加/减仓但底仓是昨天的，拆分计算。
                        # 只有当昨日底仓与今日新增方向一致时拆分才有意义；
                        # 方向相反时退化为持仓盈亏（避免负数干扰）。
                        yesterday_qty = qty - today_signed
                        same_direction = (yesterday_qty * qty) > 0 and (today_signed * qty) > 0
                        if same_direction and prev_close:
                            if qty < 0:
                                yesterday_pnl = (prev_close - current) * abs(yesterday_qty) * multiplier
                                today_pnl = (cost - current) * abs(today_signed) * multiplier
                            else:
                                yesterday_pnl = (current - prev_close) * abs(yesterday_qty) * multiplier
                                today_pnl = (current - cost) * abs(today_signed) * multiplier
                            day_pnl_val = yesterday_pnl + today_pnl
                            base_val = (
                                abs(yesterday_qty) * prev_close + abs(today_signed) * cost
                            ) * multiplier
                            day_pnl_ratio_val = day_pnl_val / base_val if base_val else 0.0
                        else:
                            day_pnl_val = pnl
                            day_pnl_ratio_val = pnl_ratio
                    else:
                        day_pnl_val = 0.0
                        day_pnl_ratio_val = 0.0
                else:
                    mkt_val = abs(qty) * current if current else 0.0
                    cost_val = abs(qty) * cost if cost else 0.0
                    pnl = mkt_val - cost_val if current else 0.0
                    pnl_ratio = pnl / cost_val if cost_val else 0.0
                    # 正股当日盈亏：美股在盘前/盘中/盘后/夜盘都计算，基准是 prev_close
                    is_us = symbol_raw.rsplit(".", 1)[-1].upper() == "US"
                    # "closed" 是夜盘 23:59 到次日盘前 04:00 ET 之间的死区，
                    # 此时 current = 收盘价 / prev_close = 昨日收盘，两者都有效，
                    # 应当计算当日盈亏。早期只在 pre/regular/post/overnight 计算导致
                    # 北京时间 12:00-16:00（≈ ET 00:00-04:00）期间美股 day_pnl 全部为 0。
                    should_calc_day_pnl = (
                        is_us and us_session in ("pre", "regular", "post", "overnight", "closed")
                    ) or symbol_is_trading_today.get(symbol_raw, False)

                    if should_calc_day_pnl and current and prev_close:
                        day_pnl_val = (current - prev_close) * qty
                        prev_val = abs(qty) * prev_close
                        day_pnl_ratio_val = day_pnl_val / prev_val if prev_val else 0.0
                    else:
                        day_pnl_val = 0.0
                        day_pnl_ratio_val = 0.0

                position = Position(
                    synced_at=datetime.utcnow(),
                    symbol=symbol_raw,
                    market=market,
                    name=str(pos.symbol_name) if hasattr(pos, "symbol_name") else "",
                    quantity=qty,
                    available_qty=int(pos.available_quantity) if hasattr(pos, "available_quantity") else 0,
                    cost_price=cost,
                    current_price=current,
                    prev_close=prev_close,
                    market_value=mkt_val,
                    unrealized_pnl=pnl,
                    unrealized_pnl_ratio=pnl_ratio,
                    day_pnl=day_pnl_val,
                    day_pnl_ratio=day_pnl_ratio_val,
                    currency=str(pos.currency) if hasattr(pos, "currency") else "HKD",
                    raw_json=json.dumps(str(pos)),
                )
                db.add(position)
                rows += 1

        db.commit()
        _finish_sync_log(db, log, "success", rows)
    except Exception as exc:
        db.rollback()
        _finish_sync_log(db, log, "error", error=f"{exc}\n{traceback.format_exc()}")
    return log


def sync_executions(db: Session) -> SyncLog:
    log = _start_sync_log(db, "executions")
    try:
        if _use_mock():
            from app.longbridge.mock_data import MOCK_EXECUTIONS
            raw_executions = MOCK_EXECUTIONS
        else:
            from app.longbridge.client import get_trade_context
            ctx = get_trade_context()
            latest = db.query(Execution).order_by(Execution.trade_done_at.desc()).first()
            start_at = latest.trade_done_at if latest else None
            history = list(ctx.history_executions(start_at=start_at))
            # history_executions 在港股盘中往往拉不到当日成交，需要 today_executions 补齐
            try:
                today = list(ctx.today_executions())
            except Exception as exc:
                logger.warning("today_executions() failed: %s", exc)
                today = []
            seen_ids: set[str] = set()
            raw_executions = []
            for e in today + history:
                if isinstance(e, dict):
                    eid = e.get("execution_id") or e.get("trade_id", "")
                else:
                    eid = str(getattr(e, "trade_id", "") or getattr(e, "execution_id", ""))
                if not eid or eid in seen_ids:
                    continue
                seen_ids.add(eid)
                raw_executions.append(e)

        rows = 0
        for exe in raw_executions:
            if isinstance(exe, dict):
                execution_id = exe["execution_id"]
                existing = db.query(Execution).filter(Execution.execution_id == execution_id).first()
                if existing:
                    continue
                symbol_raw = exe["symbol"]
                parts = symbol_raw.rsplit(".", 1)
                market = parts[1] if len(parts) > 1 else ""
                execution = Execution(
                    execution_id=execution_id,
                    order_id=exe.get("order_id", ""),
                    symbol=symbol_raw,
                    market=market,
                    side=exe.get("side", ""),
                    price=exe.get("price", 0.0),
                    quantity=exe.get("quantity", 0),
                    trade_done_at=exe.get("trade_done_at", datetime.utcnow()),
                    currency=exe.get("currency", "HKD"),
                    commission=0.0,
                    platform_fee=0.0,
                    raw_json=json.dumps(exe, ensure_ascii=False, default=str),
                )
            else:
                # 真实 SDK: trade_id 是唯一标识，无 side/currency 字段
                execution_id = str(exe.trade_id) if hasattr(exe, "trade_id") else str(getattr(exe, "execution_id", ""))
                existing = db.query(Execution).filter(Execution.execution_id == execution_id).first()
                if existing:
                    continue
                symbol_raw = str(exe.symbol) if hasattr(exe, "symbol") else ""
                parts = symbol_raw.rsplit(".", 1)
                market = parts[1] if len(parts) > 1 else ""

                # 从关联订单获取 side 和 currency
                order_id_val = str(exe.order_id) if hasattr(exe, "order_id") else ""
                side_val = ""
                currency_val = "USD" if market == "US" else "HKD"
                if order_id_val:
                    related_order = db.query(Order).filter(Order.order_id == order_id_val).first()
                    if related_order:
                        side_val = _clean_enum(related_order.side)

                execution = Execution(
                    execution_id=execution_id,
                    order_id=order_id_val,
                    symbol=symbol_raw,
                    market=market,
                    side=side_val,
                    price=float(exe.price) if hasattr(exe, "price") else 0.0,
                    quantity=int(exe.quantity) if hasattr(exe, "quantity") else 0,
                    trade_done_at=exe.trade_done_at if hasattr(exe, "trade_done_at") else datetime.utcnow(),
                    currency=currency_val,
                    commission=0.0,
                    platform_fee=0.0,
                    raw_json=json.dumps(str(exe)),
                )
            db.add(execution)
            rows += 1

        db.commit()
        _finish_sync_log(db, log, "success", rows)
    except Exception as exc:
        db.rollback()
        _finish_sync_log(db, log, "error", error=f"{exc}\n{traceback.format_exc()}")
    return log


def sync_orders(db: Session) -> SyncLog:
    log = _start_sync_log(db, "orders")
    try:
        if _use_mock():
            from app.longbridge.mock_data import MOCK_ORDERS
            raw_orders = MOCK_ORDERS
        else:
            from app.longbridge.client import get_trade_context
            ctx = get_trade_context()
            # history_orders() 不包含当日仍 New/Pending 的单，
            # 且部分券商窗口下当日已成交单也会延迟出现，需要 today_orders() 合并。
            history = list(ctx.history_orders())
            try:
                today = list(ctx.today_orders())
            except Exception as exc:
                logger.warning("today_orders() failed, falling back to history only: %s", exc)
                today = []
            seen: set[str] = set()
            raw_orders = []
            # today_orders 在前：今日最新状态优先；history 里的同 id 走 upsert 分支更新成最新
            for o in today + history:
                if isinstance(o, dict):
                    oid = o.get("order_id", "")
                else:
                    oid = str(o.order_id) if hasattr(o, "order_id") else ""
                if not oid or oid in seen:
                    continue
                seen.add(oid)
                raw_orders.append(o)

        rows = 0
        for ord in raw_orders:
            if isinstance(ord, dict):
                order_id = ord["order_id"]
                symbol_raw = ord["symbol"]
                parts = symbol_raw.rsplit(".", 1)
                market = parts[1] if len(parts) > 1 else ""

                existing = db.query(Order).filter(Order.order_id == order_id).first()
                if existing:
                    existing.status = ord.get("status", existing.status)
                    existing.filled_qty = ord.get("filled_qty", existing.filled_qty)
                    existing.avg_price = ord.get("avg_price", existing.avg_price)
                    existing.updated_at = ord.get("updated_at", datetime.utcnow())
                    existing.raw_json = json.dumps(ord, ensure_ascii=False, default=str)
                else:
                    order = Order(
                        order_id=order_id,
                        symbol=symbol_raw,
                        market=market,
                        side=ord.get("side", ""),
                        order_type=ord.get("order_type", ""),
                        status=ord.get("status", ""),
                        submitted_qty=ord.get("quantity", 0),
                        filled_qty=ord.get("filled_qty", 0),
                        avg_price=ord.get("avg_price", 0.0),
                        submitted_at=ord.get("submitted_at"),
                        updated_at=ord.get("updated_at"),
                        raw_json=json.dumps(ord, ensure_ascii=False, default=str),
                    )
                    db.add(order)
            else:
                order_id = str(ord.order_id) if hasattr(ord, "order_id") else ""
                symbol_raw = str(ord.symbol) if hasattr(ord, "symbol") else ""
                parts = symbol_raw.rsplit(".", 1)
                market = parts[1] if len(parts) > 1 else ""

                existing = db.query(Order).filter(Order.order_id == order_id).first()
                # 真实 SDK: executed_quantity/executed_price 而非 filled_qty/avg_price
                # executed_price 可能为 None（未成交的订单）
                filled = int(ord.executed_quantity) if hasattr(ord, "executed_quantity") and ord.executed_quantity is not None else 0
                avg = float(ord.executed_price) if hasattr(ord, "executed_price") and ord.executed_price is not None else 0.0
                side_val = _clean_enum(str(ord.side)) if hasattr(ord, "side") else ""
                order_type_val = _clean_enum(str(ord.order_type)) if hasattr(ord, "order_type") else ""
                status_val = _clean_enum(str(ord.status)) if hasattr(ord, "status") else ""

                if existing:
                    existing.status = status_val or existing.status
                    existing.side = side_val or existing.side
                    existing.order_type = order_type_val or existing.order_type
                    existing.filled_qty = filled
                    existing.avg_price = avg
                    existing.updated_at = ord.updated_at if hasattr(ord, "updated_at") else datetime.utcnow()
                    existing.raw_json = json.dumps(str(ord))
                else:
                    order = Order(
                        order_id=order_id,
                        symbol=symbol_raw,
                        market=market,
                        side=side_val,
                        order_type=order_type_val,
                        status=status_val,
                        submitted_qty=int(ord.quantity) if hasattr(ord, "quantity") else 0,
                        filled_qty=filled,
                        avg_price=avg,
                        submitted_at=ord.submitted_at if hasattr(ord, "submitted_at") else None,
                        updated_at=ord.updated_at if hasattr(ord, "updated_at") else None,
                        raw_json=json.dumps(str(ord)),
                    )
                    db.add(order)
            rows += 1

        db.commit()
        _finish_sync_log(db, log, "success", rows)
    except Exception as exc:
        db.rollback()
        _finish_sync_log(db, log, "error", error=f"{exc}\n{traceback.format_exc()}")
    return log


def sync_all(db: Session) -> list[SyncLog]:
    # 重置汇率缓存，确保每次同步使用最新汇率
    _reset_rate_cache()
    # 依赖顺序：
    #   orders     —— 独立
    #   executions —— 依赖 orders（用 order_id 反查 side/currency）
    #   positions  —— 依赖 executions（期权当日盈亏拆分需要今日成交）
    #   account    —— 依赖 positions（汇总持仓浮动） + executions（已实现盈亏）
    return [
        sync_orders(db),
        sync_executions(db),
        sync_positions(db),
        sync_account(db),
    ]
