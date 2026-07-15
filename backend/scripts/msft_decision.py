"""MSFT 半裸 covered call 开盘决策计算器.

场景: 持有 50 股 MSFT + 卖出 1 张 6/18 $440 call(义务 100 股 → 另 50 股裸露).
开盘后跑这个脚本, 一次性算清: 买回成本 / 净 delta / 组合盈亏 / 各 roll 净收付 /
止损线 / 到期各价位盈亏. 市场关闭时自动回退到盘前报价 + BS 理论价.

用法:
    uv run python -m scripts.msft_decision            # 同步 + 计算
    uv run python -m scripts.msft_decision --no-sync  # 跳过同步, 用现有数据
"""

import argparse
import math
import sqlite3
from datetime import date, datetime

from app.longbridge.client import get_quote_context

R = 0.043  # 无风险利率(近似)
TODAY = date.today()


# ---------- Black-Scholes(无 scipy, 用 erf) ----------
def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S: float, K: float, T: float, sigma: float, r: float = R):
    """返回 (理论价, delta). T 单位为年."""
    if T <= 0 or sigma <= 0:
        intrinsic = max(S - K, 0.0)
        return intrinsic, (1.0 if S > K else 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    price = S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return price, _ncdf(d1)


def yfrac(expiry: date) -> float:
    return max((expiry - TODAY).days, 0) / 365.0


# ---------- 报价 ----------
def spot(ctx) -> tuple[float, str]:
    """取最新 MSFT 现价, 优先用时间戳最新的那个(常规/盘前/盘后)."""
    q = ctx.quote(["MSFT.US"])[0]
    cands = [("regular", q.last_done, getattr(q, "timestamp", None))]
    for attr, tag in [("pre_market_quote", "pre"), ("post_market_quote", "post")]:
        v = getattr(q, attr, None)
        if v and getattr(v, "last_done", None):
            cands.append((tag, float(v.last_done), getattr(v, "timestamp", None)))
    cands = [c for c in cands if c[1]]
    cands.sort(key=lambda c: (c[2] or datetime.min), reverse=True)
    tag, px, ts = cands[0]
    return float(px), f"{tag}@{ts}"


def opt_mark(ctx, sym: str, S: float, K: float, expiry: date):
    """返回 (可成交价, 来源标签, iv, delta).

    开盘有实时 bid/ask 时用 mid(最准). 否则用 BS 理论价(与当前现价一致), 而不是
    last —— 盘前/盘后 last 往往是上一交易日的陈旧成交, 会和当前现价错配.
    last 仍打印出来供参考.
    """
    x = ctx.option_quote([sym])[0]
    bid = float(getattr(x, "bid", 0) or 0)
    ask = float(getattr(x, "ask", 0) or 0)
    last = float(getattr(x, "last_done", 0) or 0)
    iv = float(getattr(x, "implied_volatility", 0) or 0) or 0.30
    theo, delta = bs_call(S, K, yfrac(expiry), iv)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2, f"mid {bid}/{ask}", iv, delta
    return theo, f"BS估{theo:.2f}(陈旧last={last})", iv, delta


# ---------- 读持仓 ----------
def load_position(db_path="data/trading.db"):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    sh = c.execute(
        "select quantity,cost_price from position where symbol='MSFT.US' "
        "order by synced_at desc limit 1"
    ).fetchone()
    call = c.execute(
        "select quantity,cost_price from position where symbol='MSFT260618C440000.US' "
        "order by synced_at desc limit 1"
    ).fetchone()
    return (
        int(sh["quantity"]), float(sh["cost_price"]),
        int(call["quantity"]), float(call["cost_price"]),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-sync", action="store_true")
    ap.add_argument("--bark", action="store_true", help="把决策摘要推到 Bark")
    args = ap.parse_args()

    if not args.no_sync:
        from app.db import SessionLocal, init_db
        from app.longbridge.sync import sync_positions
        init_db()
        db = SessionLocal()
        try:
            sync_positions(db)
        finally:
            db.close()

    ctx = get_quote_context()
    shares, sh_cost, call_qty, call_prem = load_position()
    mult = 100
    short_n = -call_qty  # 卖出张数(正)
    K = 440.0
    expiry = date(2026, 6, 18)

    S, src = spot(ctx)
    cur_px, cur_src, cur_iv, cur_delta = opt_mark(
        ctx, "MSFT260618C440000.US", S, K, expiry
    )

    print("=" * 64)
    print(f"  MSFT 决策表   {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 64)
    print(f"现价 S = ${S:.2f}   (来源 {src})")
    print(f"持仓: {shares} 股 @ 成本 {sh_cost:.2f}   |   卖出 {short_n} 张 ${K:.0f}C "
          f"6/18 @ 权利金 {call_prem:.2f}")
    print()

    # 当前 440 call
    intrinsic = max(S - K, 0)
    tv = cur_px - intrinsic
    buyback = cur_px * mult * short_n
    print("── 当前那张 440 call ──")
    print(f"  可成交价   : {cur_px:.2f}   (来源 {cur_src}, IV {cur_iv:.0%})")
    print(f"  内在/时间  : 内在 {intrinsic:.2f}  +  时间价值 {tv:.2f}")
    print(f"  买回平仓要 : ${buyback:,.0f}")
    print(f"  call delta : {cur_delta:.2f}")
    print()

    # 组合盈亏 + 净 delta
    sh_pnl = shares * (S - sh_cost)
    opt_pnl = short_n * mult * (call_prem - cur_px)
    net_delta = shares - cur_delta * mult * short_n
    print("── 组合现状 ──")
    print(f"  正股浮盈    : {sh_pnl:+,.0f}")
    print(f"  期权浮盈    : {opt_pnl:+,.0f}")
    print(f"  合计        : {sh_pnl + opt_pnl:+,.0f}")
    print(f"  净 delta    : {net_delta:+.0f}   "
          f"({'每涨1块亏' if net_delta < 0 else '每涨1块赚'} ~${abs(net_delta):.0f})")
    print()

    # 到期盈亏(theta=0)
    print("── 到期(6/18)总盈亏 @ 不同 MSFT 价 ──")
    # 固定档位中去掉与现价相邻(±2)的, 避免和"现价行"重复
    fixed = [p for p in [430, 440, 450, 460, 470, 475, 480, 490, 500] if abs(p - S) > 2]
    rows = sorted(fixed + [round(S, 1)])
    for s in rows:
        spnl = shares * (s - sh_cost)
        opnl = short_n * mult * (call_prem - max(s - K, 0))
        tot = spnl + opnl
        flag = "  ← 现价" if abs(s - S) < 0.01 else ""
        print(f"  S={s:>6.1f}:  正股 {spnl:>+7.0f}  期权 {opnl:>+8.0f}  合计 {tot:>+8.0f}{flag}")
    # 上行盈亏平衡
    # 0 = shares*(s-cost) + short_n*100*(prem-(s-K))  -> 解 s
    a = shares - short_n * mult
    b = -shares * sh_cost + short_n * mult * (call_prem + K)
    if a != 0:
        be = -b / a
        print(f"  上行盈亏平衡 ≈ S={be:.1f}  (站上这里以上, 到期就净亏)")
    print()

    # roll 候选
    print("── Roll 候选 (买回 6/18 440C, 卖出新 call) ──")
    cands = [
        ("MSFT260717C460000.US", 460, date(2026, 7, 17)),
        ("MSFT260717C470000.US", 470, date(2026, 7, 17)),
        ("MSFT260821C470000.US", 470, date(2026, 8, 21)),
        ("MSFT260821C480000.US", 480, date(2026, 8, 21)),
        ("MSFT260821C490000.US", 490, date(2026, 8, 21)),
    ]
    print(f"  {'新合约':<22}{'卖价':>8}{'净现金':>10}{'roll后净delta':>16}")
    for sym, nk, nexp in cands:
        try:
            npx, nsrc, niv, ndelta = opt_mark(ctx, sym, S, nk, nexp)
        except Exception as e:
            print(f"  {sym:<22} 取价失败: {e}")
            continue
        net_cash = (npx - cur_px) * mult * short_n  # >0 净收, <0 净付
        roll_delta = shares - ndelta * mult * short_n
        tag = "净收" if net_cash >= 0 else "净付"
        print(f"  {sym:<22}{npx:>8.2f}{net_cash:>+10.0f}{roll_delta:>+12.0f}     "
              f"[{tag} {abs(net_cash):.0f}, {nsrc}]")
    print()

    # ── A/B/C 三路到期盈亏对比(从建仓算起)──
    prem0 = call_prem * mult * short_n          # 已收权利金(+501)
    buyback_cost = cur_px * mult * short_n       # 现在买回 440C 的花费
    cK, cKstk, cExp = ("MSFT260821C480000.US", 480.0, date(2026, 8, 21))  # C 方案: roll 到 8月480
    try:
        cpx, csrc, _, cdelta = opt_mark(ctx, cK, S, cKstk, cExp)
    except Exception:
        cpx, csrc, cdelta = 0.0, "取价失败", 0.0
    c_new_prem = cpx * mult * short_n            # 卖新 call 收的权利金

    def pnl_A(s):  # 啥都不动, 6/18
        return shares * (s - sh_cost) + prem0 - mult * short_n * max(s - K, 0)

    def pnl_B(s):  # 现在平掉 440C, 剩 50 股
        return shares * (s - sh_cost) + prem0 - buyback_cost

    def pnl_C(s):  # roll 到 8月 cKstk
        return (shares * (s - sh_cost) + prem0 - buyback_cost + c_new_prem
                - mult * short_n * max(s - cKstk, 0))

    print(f"── 三路到期盈亏对比 (买回440C≈${buyback_cost:,.0f}, "
          f"C=roll到{cKstk:.0f}C {cExp:%m/%d} 收权利金≈${c_new_prem:,.0f}) ──")
    print(f"  {'MSFT价':>8}{'A 不动':>10}{'B 平掉':>10}{'C roll480':>11}")
    grid = sorted({420, 440, 460, 470, 480, 490, 500, 520, round(S)})
    for s in grid:
        flag = " ←现价" if abs(s - S) < 1 else ""
        print(f"  {s:>8.0f}{pnl_A(s):>+10.0f}{pnl_B(s):>+10.0f}{pnl_C(s):>+11.0f}{flag}")
    print(f"  {'净delta':>8}{net_delta:>+10.0f}{shares:>+10.0f}"
          f"{shares - cdelta * mult * short_n:>+11.0f}")
    print(f"  A 守440(6/18) | B 纯多50股(看涨,与'不暴涨'相左) | C 重置{cKstk:.0f}(8/21,新call卖价{csrc})")
    print()
    print("注: 市场关闭时卖价是 last/BS 估算; 开盘后会用 mid(bid,ask), 以开盘后为准.")

    if args.bark:
        from app.services.notify import send_bark
        c_cash = c_new_prem - buyback_cost  # roll 今天净现金(<0 净付)
        body = (
            f"S=${S:.2f} | 组合 {sh_pnl + opt_pnl:+,.0f} | 净delta {net_delta:+.0f}\n"
            f"A不动:守440,平衡~475 | C roll480:今天{'净收' if c_cash>=0 else '净付'}${abs(c_cash):,.0f},净delta掰回~{shares - cdelta*mult*short_n:+.0f}\n"
            f"B平掉=看涨赌(与你判断相左) | 收盘站上475=认错线"
        )
        r = send_bark("MSFT 期权决策", body, sound="bell",
                      level="timeSensitive", group="trading-alerts")
        print(f"\n[Bark] {'已推送 ✅' if r.get('ok') else '推送失败: ' + str(r.get('detail'))}")


if __name__ == "__main__":
    main()
