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

_cached_usd_hkd_rate: float | None = None

def _get_usd_hkd_rate() -> float:
    """获取 USD/HKD 实时汇率，带缓存（每次 sync_all 调用时重置）"""
    global _cached_usd_hkd_rate
    if _cached_usd_hkd_rate is not None:
        return _cached_usd_hkd_rate

    # 尝试从公开 API 获取实时汇率
    fallback_rate = 7.8315
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        req = urllib.request.Request(url, headers={"User-Agent": "trading-app/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if data.get("result") == "success" and "rates" in data:
                rate = float(data["rates"].get("HKD", fallback_rate))
                logger.info("USD/HKD rate from API: %.4f", rate)
                _cached_usd_hkd_rate = rate
                return rate
    except Exception as exc:
        logger.warning("Failed to fetch USD/HKD rate: %s, using fallback %.4f", exc, fallback_rate)

    _cached_usd_hkd_rate = fallback_rate
    return fallback_rate

def _reset_rate_cache() -> None:
    """重置汇率缓存，在每次完整同步时调用"""
    global _cached_usd_hkd_rate
    _cached_usd_hkd_rate = None

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

                # 获取 USD/HKD 实时汇率
                usd_to_hkd = _get_usd_hkd_rate()

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
                day_pnl_val = 0.0
                for currency, pnl, day_pnl in currency_pnl:
                    pnl_float = float(pnl or 0)
                    day_pnl_float = float(day_pnl or 0)
                    if currency == "USD":
                        total_pnl_val += pnl_float * usd_to_hkd
                        day_pnl_val += day_pnl_float * usd_to_hkd
                    else:
                        total_pnl_val += pnl_float
                        day_pnl_val += day_pnl_float

                snapshot = AccountSnapshot(
                    synced_at=datetime.utcnow(),
                    currency=str(balance.currency) if hasattr(balance, "currency") else "HKD",
                    total_cash=total_cash_val,
                    net_assets=net_assets_val,
                    market_value=market_value_val,
                    total_pnl=float(total_pnl_val),
                    day_pnl=float(day_pnl_val),
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

            if stock_symbols:
                try:
                    from app.services.quote import _us_session  # 复用美股 session 判断
                    quote_ctx = get_quote_context()
                    quotes = quote_ctx.quote(stock_symbols)
                    us_session = _us_session(now_utc)
                    for q in quotes:
                        sym = str(q.symbol)
                        regular_last_done = float(q.last_done) if hasattr(q, "last_done") else 0.0
                        prev_close_val = float(q.prev_close) if hasattr(q, "prev_close") else 0.0
                        is_today = _is_quote_from_today(q, now_utc)

                        current_price = regular_last_done
                        is_us = sym.rsplit(".", 1)[-1].upper() == "US"

                        # 当日盈亏基准
                        is_us = sym.rsplit(".", 1)[-1].upper() == "US"
                        if is_us:
                            # 美股：根据时段选择正确的16:00收盘价作为基准
                            if us_session == "pre":
                                day_pnl_base = regular_last_done  # 盘前：last_done是昨天16:00收盘
                            elif us_session == "regular":
                                day_pnl_base = prev_close_val  # 盘中：prev_close是昨天16:00收盘
                            else:  # post, overnight
                                day_pnl_base = regular_last_done  # 盘后/夜盘：last_done是今天16:00收盘
                        else:
                            # 港股等：用 prev_close
                            day_pnl_base = prev_close_val

                        # 美股按时段选当前价：盘前 → pre / 盘中 → last_done / 盘后 → post
                        # 用时段判断而非 "字段存在 + 值 > 0"，避免命中残留数据
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
                                # closed：优先用最近的盘后价反映最新行情，否则用 last_done
                                current_price = post_last or regular_last_done or pre_last

                        symbol_prices[sym] = current_price
                        symbol_prev_close[sym] = day_pnl_base
                        symbol_is_trading_today[sym] = is_today

                    # 补充：夜盘/收盘时段用 Nasdaq 更新美股价格
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

                    # 期权当日盈亏精确计算（核心难点：长桥返回的 prev_close 是合约自身
                    # 昨日收盘价，而用户昨天可能根本没持有这个合约，直接用 prev_close
                    # 推导当日盈亏会严重失真）。
                    #
                    # 判断策略（按优先级）：
                    #   1. 今日净成交量 == 当前持仓量 → 今天全新开仓 → 当日盈亏 = 持仓盈亏
                    #   2. 今日净成交量 == 0 + 成本与 prev_close 差异巨大（>30%）
                    #      → 长桥 history_executions 拉不到今日成交（接口窗口限制），
                    #        但成本和昨收差异如此之大说明用户不是按 prev_close 接的货，
                    #        实际上是今天才开的仓 → 当日盈亏 = 持仓盈亏
                    #   3. 今日净成交量 == 0 + 成本接近 prev_close → 昨天就持有 → 用 prev_close 推导
                    #   4. 混合情况：今日加/减仓但底仓是昨天的 → 拆分计算
                    today_signed = option_today_filled_qty.get(symbol_raw, 0)
                    cost_prev_diverge = (
                        prev_close > 0 and cost > 0
                        and abs(cost - prev_close) / prev_close > 0.3
                    )

                    if current and qty != 0 and today_signed == qty:
                        # 情况 1：今日全新开/翻仓
                        day_pnl_val = pnl
                        day_pnl_ratio_val = pnl_ratio
                    elif current and qty != 0 and today_signed == 0 and cost_prev_diverge:
                        # 情况 2：今日成交数据缺失 + 成本与昨收差异巨大 → 视为今日新开仓
                        day_pnl_val = pnl
                        day_pnl_ratio_val = pnl_ratio
                    elif current and prev_close and today_signed == 0:
                        # 情况 3：今日没成交 + 成本贴近昨收 → 昨天就持有
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
                    should_calc_day_pnl = (
                        is_us and us_session in ("pre", "regular", "post", "overnight")
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
            raw_executions = ctx.history_executions(start_at=start_at)

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
            raw_orders = ctx.history_orders()

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
    # 先同步持仓，再同步账户（账户的 total_pnl 从持仓汇总计算）
    return [
        sync_positions(db),
        sync_account(db),
        sync_orders(db),
        sync_executions(db),
    ]
