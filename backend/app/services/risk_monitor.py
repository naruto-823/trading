"""Read-only portfolio monitoring with persistent alert deduplication."""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.monitor_state import MonitorState

UTC = timezone.utc
BJT = ZoneInfo("Asia/Shanghai")
OPTION = re.compile(r"^(.+?)(\d{6})([CP])(\d+)\.US$")
SESSION_LABEL = {"regular": "正式盘", "pre": "盘前", "post": "盘后", "overnight": "夜盘"}


def day_key(now: datetime) -> str:
    return (now.astimezone(BJT) - timedelta(hours=16)).date().isoformat()


def session_at(market: str, now: datetime, days: set, half_days: set) -> tuple[str, str]:
    """Use exchange trading dates, not weekday guesses; DST follows IANA zones."""
    local = now.astimezone(ZoneInfo("America/New_York") if market == "US" else BJT)
    day = local.date()
    minute = local.hour * 60 + local.minute
    if market == "US" and minute >= 1200:
        following = day + timedelta(days=1)
        return ("overnight", str(following)) if following in days else ("closed", str(day))
    if day not in days:
        return "closed", str(day)
    if market == "HK":
        active = 570 <= minute < 720 or (day not in half_days and 780 <= minute < 960)
        return ("regular" if active else "closed"), str(day)
    close = 780 if day in half_days else 960
    if minute < 240:
        return "overnight", str(day)
    if minute < 570:
        return "pre", str(day)
    if minute < close:
        return "regular", str(day)
    if minute < (1020 if day in half_days else 1200):
        return "post", str(day)
    return "closed", str(day)


def select_quote(q, session: str, trading_day: str, now: datetime) -> dict | None:
    if session == "closed" or str(getattr(q, "trade_status", "Normal")).rsplit(".", 1)[-1] != "Normal":
        return None
    selected = (
        q
        if session == "regular"
        else getattr(
            q,
            {
                "pre": "pre_market_quote",
                "post": "post_market_quote",
                "overnight": "overnight_quote",
            }[session],
            None,
        )
    )
    if selected is None:
        return None
    stamp = getattr(selected, "timestamp", None)
    if not isinstance(stamp, datetime):
        return None
    # SDK naive timestamps represent the machine's local timezone.
    stamp = stamp.astimezone(UTC)
    if not -5 <= (now - stamp).total_seconds() <= 180:
        return None
    price = float(getattr(selected, "last_done", 0) or 0)
    reference = float(getattr(q, "prev_close" if session == "regular" else "last_done", 0) or 0)
    if price <= 0 or reference <= 0:
        return None
    return {
        "symbol": str(q.symbol),
        "price": price,
        "reference": reference,
        "at": stamp.timestamp(),
        "session": session,
        "session_key": f"{trading_day}:{session}",
    }


def external_cashflow(rows, usd_hkd: float = 7.85) -> float | None:
    """Unknown labels/currencies suppress NAV attribution instead of inventing returns."""
    external = {"deposit", "withdrawal", "cash deposit", "cash withdrawal", "存入资金", "提取资金"}
    internal = {
        "buy contract-stocks",
        "sell contract-stocks",
        "exercise stock option (buy stock)",
        "exercise stock option (sell stock)",
        "option purchase transaction",
        "option sell transaction",
        "option purchase fee",
        "option sell fee",
        "stock sell commission",
        "stock buy commission",
        "stock trade fee",
        "dividend",
        "cash dividend",
        "interest",
        "financing interest",
        "currency exchange",
        "兑换货币",
        "股息",
        "融资利息",
    }
    total = 0.0
    for row in rows:
        name = str(row.transaction_flow_name).strip().lower()
        if name in internal:
            continue
        if name not in external or row.currency not in ("HKD", "USD"):
            return None
        total += float(row.balance) * (usd_hkd if row.currency == "USD" else 1)
    return total


def evaluate(snapshot: dict, previous: dict) -> tuple[list[dict], dict]:
    state = copy.deepcopy(previous)
    now = datetime.fromtimestamp(snapshot["now"], UTC)
    key = day_key(now)
    signals: list[dict] = []

    def add(code, level, title, body):
        signals.append(
            {
                "key": code,
                "level": level,
                "title": title,
                "body": body + f"\n北京时间{now.astimezone(BJT):%m-%d %H:%M:%S}。仅提醒，不自动交易。",
            }
        )

    def recover(code):
        old = state.get("sent", {}).get(code)
        if old and snapshot["now"] - old["at"] >= 1800:
            state["sent"].pop(code)

    baseline = state.get("baseline")
    if not baseline or baseline["key"] != key:
        baseline = {"key": key, "at": snapshot["now"], "nav": snapshot["net_assets"]}
        state["baseline"] = baseline
    if snapshot["margin_call"]:
        add(f"margin:{key}", 2, "🔴 券商出现追保提示", "请立即打开券商核对保证金、未成交订单和接货义务。")
    if snapshot["net_assets"] <= 0:
        add(
            f"equity-nonpositive:{key}",
            2,
            "🔴 券商净资产非正",
            "请立即核对券商净资产、保证金和负债。比例估算暂停；请直接查看券商风险提示。",
        )
    flow = snapshot["external_flow"]
    if flow is None:
        add(
            f"coverage:cashflow:{key}",
            1,
            "🟡 回撤监控口径受限",
            "资金流水缺失或出现未识别类型；暂停精确账户回撤告警，价格与追保监控继续。",
        )
    elif baseline["nav"] > 0:
        loss = (snapshot["net_assets"] - baseline["nav"] - flow) / baseline["nav"] * 100
        if loss <= -1.5:
            add(
                f"account-loss:{key}",
                2 if loss <= -3 else 1,
                "🟠 账户回撤触发提醒",
                f"相对本轮实际采集基准，剔除已识别出入金后变化{loss:.2f}%。汇率按{snapshot['usd_hkd']}估算；不是券商正式日收益。先暂停新增风险，核对主要持仓。",
            )
        elif loss > -0.75:
            recover(f"account-loss:{key}")
    old_positions = state.get("positions", {})
    pending = state.setdefault("pending", {})
    for symbol, position in snapshot["positions"].items():
        qty = position["quantity"]
        old_qty = old_positions.get(symbol, {}).get("quantity", 0)
        if OPTION.match(symbol) and qty < min(old_qty, 0):
            signal_key = f"short:{symbol}:{snapshot['now']}:{abs(qty)}"
            pending[signal_key] = {
                "key": signal_key,
                "level": 2,
                "title": "🟠 检测到净卖出期权义务增加" if "positions" in state else "🟠 首次采集到净卖出期权义务",
                "body": (
                    f"{symbol}当前净持仓{qty:g}张。请核对方向、乘数、到期日及现金/正股覆盖；"
                    f"接货金额尚未核验。\n采集北京时间{now.astimezone(BJT):%m-%d %H:%M:%S}。仅提醒，不自动交易。"
                ),
            }
    for signal in list(pending.values()):
        signals.append(signal)
    state["positions"] = snapshot["positions"]
    history = state.setdefault("history", {})
    for p in snapshot["prices"]:
        symbol = p["symbol"]
        label = SESSION_LABEL[p["session"]]
        change = (p["price"] / p["reference"] - 1) * 100
        threshold = 2.5 if symbol == "QQQ.US" else (6 if symbol.endswith(".HK") else 4)
        position = snapshot["positions"].get(symbol, {})
        weight = p["price"] * position.get("quantity", 0)
        weight *= snapshot["usd_hkd"] if position.get("currency") == "USD" else 1
        impact = weight / snapshot["net_assets"] if snapshot["net_assets"] > 0 else 0
        context = (
            f"{symbol} {label}现价{p['price']:.3f}，相对参考正式收盘{change:+.2f}%。"
            f"\n直接净敞口估计占净资产{impact * 100:.2f}%；该标的再跌1%账户约变化{-impact:+.2f}%。不含QQQ间接重叠。"
        )
        if change <= -threshold:
            add(
                f"price:{symbol}:{p['session_key']}",
                2 if change <= -threshold * 2 else 1,
                f"🟠 {symbol} {label}跌幅异常",
                context + "\n原因尚未核实，先检查公告与大盘，不据此直接追单。",
            )
        elif change > -threshold / 2:
            recover(f"price:{symbol}:{p['session_key']}")
        samples = [
            x
            for x in history.get(symbol, [])
            if x["session_key"] == p["session_key"] and 0 <= p["at"] - x["at"] <= 1080
        ]
        minutes, rapid_limit = (5, 1) if symbol == "QQQ.US" else (15, 2.5)
        eligible = [x for x in samples if minutes * 60 <= p["at"] - x["at"] <= minutes * 60 + 90]
        if eligible:
            start = max(eligible, key=lambda x: x["at"])
            interval = sorted([x["at"] for x in samples if x["at"] >= start["at"]] + [p["at"]])
            complete = all(b - a <= 180 for a, b in zip(interval, interval[1:]))
            drop = (p["price"] / start["price"] - 1) * 100
            if complete and drop <= -rapid_limit:
                add(
                    f"rapid:{symbol}:{p['session_key']}",
                    2 if drop <= -rapid_limit * 2 else 1,
                    f"🟠 {symbol} {minutes}分钟快速下跌",
                    f"窗口变化{drop:.2f}%。\n" + context,
                )
            elif complete and drop > -rapid_limit / 2:
                recover(f"rapid:{symbol}:{p['session_key']}")
        history[symbol] = samples + ([p] if not samples or p["at"] > samples[-1]["at"] else [])
    for issue in snapshot["issues"]:
        add(
            f"coverage:{issue}:{key}",
            1,
            "🟡 监控覆盖受限",
            f"{issue}：未取得新鲜有效数据，不将旧报价伪装为实时；其余可用监控继续。",
        )
    return signals, state


def deliver(signals: list[dict], state: dict, now: float, sender) -> int:
    sent = state.setdefault("sent", {})
    fired = 0
    candidates = {}
    for s in signals:
        if s["key"] not in candidates or s["level"] > candidates[s["key"]]["level"]:
            candidates[s["key"]] = s
    for s in candidates.values():
        old = sent.get(s["key"])
        if old and old["level"] >= s["level"]:
            state.setdefault("pending", {}).pop(s["key"], None)
            continue
        result = sender(
            s["title"], s["body"], level="timeSensitive" if s["level"] >= 2 else "active", group="portfolio-risk"
        )
        if result.get("ok"):
            sent[s["key"]] = {"level": s["level"], "at": now}
            state.setdefault("pending", {}).pop(s["key"], None)
            fired += 1
        else:
            state.setdefault("pending", {})[s["key"]] = s
            state["last_push_failure"] = now
    state["sent"] = {k: v for k, v in sent.items() if now - v["at"] < 7 * 86400}
    if not state.get("pending"):
        state.pop("last_push_failure", None)
    return fired


def load_state(db: Session) -> dict:
    row = db.get(MonitorState, "portfolio-risk")
    return json.loads(row.value) if row else {}


def save_state(db: Session, state: dict) -> None:
    row = db.get(MonitorState, "portfolio-risk")
    if row is None:
        row = MonitorState(key="portfolio-risk", value="{}")
        db.add(row)
    row.value = json.dumps(state, ensure_ascii=False)
    db.commit()


def health(db: Session, now: float) -> dict:
    state = load_state(db)
    configured = state.get("configured", False)
    enabled = state.get("enabled", False)
    recent = now - state.get("last_success", 0) <= 300
    return {
        "healthy": bool(configured and enabled and recent and not state.get("last_push_failure")),
        "coverage_complete": not bool(state.get("coverage_issues") or state.get("error_type")),
        "enabled": enabled,
        "bark_configured": configured,
        "last_poll": state.get("last_poll"),
        "last_success": state.get("last_success"),
        "error_type": state.get("error_type"),
        "coverage_issues": state.get("coverage_issues", []),
        "pending_pushes": len(state.get("pending", {})),
    }
