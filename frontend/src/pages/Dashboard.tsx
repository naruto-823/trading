import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Wallet, TrendingUp, BarChart3, DollarSign, CalendarDays, Wifi, WifiOff, RotateCcw } from "lucide-react";
import {
  getAccount,
  getPositions,
  syncAll,
  getSyncLogs,
  type QuoteData,
} from "@/api/client";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import MarketStatus from "@/components/MarketStatus";
import BriefingCard from "@/components/BriefingCard";
import SchedulerStatus from "@/components/SchedulerStatus";
import { useQuoteWebSocket } from "@/hooks/useQuoteWebSocket";
import { formatCurrency, formatPercent, pnlColor } from "@/lib/utils";
import { evaluatePosition } from "@/lib/positionRules";

type QuoteMap = Record<string, QuoteData>;

export default function Dashboard() {
  const queryClient = useQueryClient();
  const hasAutoSynced = useRef(false);

  const accountQuery = useQuery({
    queryKey: ["account"],
    queryFn: () => getAccount(),
  });

  const positionsQuery = useQuery({
    queryKey: ["positions"],
    queryFn: () => getPositions(),
  });

  const syncLogsQuery = useQuery({
    queryKey: ["syncLogs"],
    queryFn: () => getSyncLogs(5),
  });

  const syncMutation = useMutation({
    mutationFn: syncAll,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["account"] });
      queryClient.invalidateQueries({ queryKey: ["positions"] });
      queryClient.invalidateQueries({ queryKey: ["syncLogs"] });
    },
  });

  // 页面加载时自动同步一次
  useEffect(() => {
    if (!hasAutoSynced.current) {
      hasAutoSynced.current = true;
      syncMutation.mutate();
    }
  }, []);

  const account = accountQuery.data?.data;
  const allPositions = positionsQuery.data?.data ?? [];
  const syncLogs = syncLogsQuery.data?.data ?? [];
  const lastSync = syncLogs[0];

  // 按市场分组，按市值降序（仓位大小）排列
  const hkPositions = allPositions
    .filter((p) => p.market === "HK")
    .sort((a, b) => Math.abs(b.market_value) - Math.abs(a.market_value));
  const usStockPositions = allPositions
    .filter((p) => p.market === "US" && p.symbol.length <= 8)
    .sort((a, b) => Math.abs(b.market_value) - Math.abs(a.market_value));
  const usOptionPositions = allPositions
    .filter((p) => p.market === "US" && p.symbol.length > 8)
    .sort((a, b) => Math.abs(b.market_value) - Math.abs(a.market_value));

  // 通过 WebSocket 实时订阅美股报价（含盘前/盘后），服务端每 3 秒推送
  const usStockSymbols = usStockPositions.map((p) => p.symbol);
  const { quoteMap: usQuoteMap, status: wsStatus } = useQuoteWebSocket(usStockSymbols);

  // 用实时报价聚合顶部账户卡片的总盈亏 / 当日盈亏
  // USD → HKD 用账户快照里的真实汇率（来自 LB SDK 实时拉取）；账户没快照时不强算
  const usdHkd = account?.fx_rates?.USD_HKD ?? 0;
  const liveTotalPnl = useMemo(() => {
    if (!account || !usdHkd) return null;
    // 港股持仓：用 DB 快照（港股无盘前盘后，快照即最新）
    const hkPnl = hkPositions.reduce((sum, pos) => sum + pos.unrealized_pnl, 0);
    // 美股正股：用实时价回算
    const usPnl = usStockPositions.reduce((sum, pos) => {
      const quote = usQuoteMap[pos.symbol];
      const livePrice = quote?.current_price && quote.current_price > 0
        ? quote.current_price
        : pos.current_price;
      const pnl = (livePrice - pos.cost_price) * pos.quantity;
      return sum + pnl * usdHkd;
    }, 0);
    // 期权：用 DB 快照（期权无盘前盘后报价）
    const optionPnl = usOptionPositions.reduce(
      (sum, pos) => sum + pos.unrealized_pnl * usdHkd,
      0
    );
    return hkPnl + usPnl + optionPnl;
  }, [hkPositions, usStockPositions, usOptionPositions, usQuoteMap, account, usdHkd]);

  const liveDayPnl = useMemo(() => {
    if (!account || !usdHkd) return null;
    const hkDayPnl = hkPositions.reduce((sum, pos) => sum + pos.day_pnl, 0);
    const usDayPnl = usStockPositions.reduce((sum, pos) => {
      const quote = usQuoteMap[pos.symbol];
      const livePrice = quote?.current_price && quote.current_price > 0
        ? quote.current_price
        : pos.current_price;
      const prevClose = pos.prev_close ?? 0;
      const dayPnl = prevClose > 0 ? (livePrice - prevClose) * pos.quantity : pos.day_pnl;
      return sum + dayPnl * usdHkd;
    }, 0);
    const optionDayPnl = usOptionPositions.reduce(
      (sum, pos) => sum + pos.day_pnl * usdHkd,
      0
    );
    // 已卖出标的的当日贡献：来自账户快照，按市场原币 → HKD
    const realizedByMarket = account.realized_day_pnl_by_market ?? {};
    const realizedHkd = Object.entries(realizedByMarket).reduce((sum, [market, amount]) => {
      if (market === "US") return sum + amount * usdHkd;
      return sum + amount;
    }, 0);
    return hkDayPnl + usDayPnl + optionDayPnl + realizedHkd;
  }, [hkPositions, usStockPositions, usOptionPositions, usQuoteMap, account, usdHkd]);

  // 今日已卖出标的对当日盈亏的贡献（原币、按市场拆分）。Position 表丢失了已卖完的标的，
  // 这里要单独叠加进对应市场卡片的 dayPnl 总和。
  const soldTodayByMarket = account?.realized_day_pnl_by_market ?? {};
  const hkSoldToday = soldTodayByMarket["HK"] ?? 0;
  const usSoldToday = soldTodayByMarket["US"] ?? 0;

  // 用于计算持仓集中度：把所有持仓的市值统一归一到 HKD
  const totalPortfolioHkd = useMemo(() => {
    const hk = hkPositions.reduce((s, p) => s + Math.abs(p.market_value), 0);
    const us = usdHkd
      ? [...usStockPositions, ...usOptionPositions].reduce(
          (s, p) => s + Math.abs(p.market_value) * usdHkd,
          0,
        )
      : 0;
    return hk + us;
  }, [hkPositions, usStockPositions, usOptionPositions, usdHkd]);

  // 顶部卡片显示币种切换（HKD / CNY / USD），后端记账始终用 HKD
  // HKD 是 source of truth（跟 LB APP 完全一致）；CNY / USD 走 fx 服务的实时汇率，
  // 但因为 LB APP 内部 CNH 显示用了略微不同的 fx 源，CNH 显示会有 ~0.2% 的 noise。
  type DisplayCurrency = "HKD" | "CNY" | "USD";
  const [displayCurrency, setDisplayCurrency] = useState<DisplayCurrency>(() => {
    const saved = (typeof window !== "undefined" && localStorage.getItem("dashboard.displayCurrency")) as DisplayCurrency | null;
    return saved === "CNY" || saved === "USD" ? saved : "HKD";
  });
  useEffect(() => {
    if (typeof window !== "undefined") {
      localStorage.setItem("dashboard.displayCurrency", displayCurrency);
    }
  }, [displayCurrency]);

  // 把 HKD 值转成当前展示币种 — 用账户快照里的真实汇率，没快照时不换算（直接显示 HKD 值）
  const fxRates = account?.fx_rates ?? {};
  const fromHkd = (hkd: number): number => {
    if (displayCurrency === "HKD") return hkd;
    if (displayCurrency === "CNY" && fxRates.HKD_CNY) return hkd * fxRates.HKD_CNY;
    if (displayCurrency === "USD" && fxRates.USD_HKD) return hkd / fxRates.USD_HKD;
    return hkd;
  };

  // 长桥 account_balance 不含 IPO 申购冻结的钱：申购款已离开 total_cash，
  // 新股配发上市前也不在持仓里 —— 这笔钱会从净资产/现金整笔消失。
  // 后端从 cash_flow 还原出未配发申购占款（pending_ipo），这里加回，与长桥 APP 口径一致。
  const pendingIpo = account?.pending_ipo ?? 0;
  const totalAssets = (account?.net_assets ?? 0) + pendingIpo;
  const displayCash = (account?.total_cash ?? 0) + pendingIpo;

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="space-y-1.5">
          <h2 className="text-2xl font-bold tracking-tight">Dashboard</h2>
          <div className="flex items-center gap-3 flex-wrap">
            <p className="text-muted-foreground text-sm">
              {lastSync
                ? `最近同步: ${new Date(lastSync.started_at).toLocaleString("zh-CN")}`
                : "暂未同步"}
            </p>
            <span className="text-muted-foreground text-xs">·</span>
            <MarketStatus />
            <span className="text-muted-foreground text-xs">·</span>
            <WsStatusBadge status={wsStatus} />
          </div>
        </div>
        <Button
          onClick={() => syncMutation.mutate()}
          disabled={syncMutation.isPending}
          variant="outline"
        >
          <RefreshCw
            className={`h-4 w-4 mr-2 ${syncMutation.isPending ? "animate-spin" : ""}`}
          />
          {syncMutation.isPending ? "同步中..." : "同步数据"}
        </Button>
      </div>

      {/* 后台调度状态：折叠 */}
      <SchedulerStatus />

      {/* AI 复盘卡片：顶部，可折叠 */}
      <BriefingCard />

      {/* Account Cards */}
      {account && (
        <>
          {/* 顶部币种切换：HKD 是 source of truth（跟 LB APP 一致），CNY/USD 为换算显示 */}
          <div className="flex items-center justify-end gap-2 text-xs">
            <span className="text-muted-foreground">显示币种</span>
            <div className="inline-flex rounded-md border bg-background overflow-hidden">
              {(["HKD","CNY","USD"] as const).map((c) => (
                <button
                  key={c}
                  onClick={() => setDisplayCurrency(c)}
                  className={`px-2.5 py-1 transition-colors ${
                    displayCurrency === c
                      ? "bg-foreground text-background font-medium"
                      : "hover:bg-muted text-muted-foreground"
                  }`}
                  title={
                    c === "HKD"
                      ? "账户主币，跟长桥 APP 完全一致"
                      : c === "CNY"
                      ? "按 fx 服务实时汇率换算；与 LB APP 内部显示有 ~0.2% noise（不同 fx 源）"
                      : "按 fx 服务实时汇率换算"
                  }
                >
                  {c}
                </button>
              ))}
            </div>
            {displayCurrency !== "HKD" && fxRates.HKD_CNY && fxRates.USD_HKD && (
              <span className="text-muted-foreground ml-1">
                · 1 HKD = {(displayCurrency === "CNY" ? fxRates.HKD_CNY : 1 / fxRates.USD_HKD).toFixed(4)} {displayCurrency}
                {displayCurrency === "CNY" && (
                  <span className="text-muted-foreground/70"> · 与 APP 有 ~0.2% noise</span>
                )}
              </span>
            )}
          </div>

          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">总资产</CardTitle>
              <Wallet className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {formatCurrency(fromHkd(totalAssets), displayCurrency)}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">总市值</CardTitle>
              <BarChart3 className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {formatCurrency(fromHkd(account.market_value), displayCurrency)}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">现金</CardTitle>
              <DollarSign className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {formatCurrency(fromHkd(displayCash), displayCurrency)}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">总盈亏</CardTitle>
              <TrendingUp className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className={`text-2xl font-bold ${pnlColor(liveTotalPnl ?? account.total_pnl)}`}>
                {formatCurrency(fromHkd(liveTotalPnl ?? account.total_pnl), displayCurrency)}
              </div>
              {liveTotalPnl !== null && liveTotalPnl !== account.total_pnl && (
                <p className="text-xs text-muted-foreground mt-1">实时估算</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">当日盈亏</CardTitle>
              <CalendarDays className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className={`text-2xl font-bold ${pnlColor(liveDayPnl ?? account.day_pnl)}`}>
                {formatCurrency(fromHkd(liveDayPnl ?? account.day_pnl), displayCurrency)}
              </div>
              {liveDayPnl !== null && liveDayPnl !== account.day_pnl && (
                <p className="text-xs text-muted-foreground mt-1">实时估算</p>
              )}
            </CardContent>
          </Card>

          {(() => {
            const debt = account.outstanding_debt ?? 0;
            const hasDebt = debt < 0;
            const debtPctOfTotal = hasDebt && totalAssets > 0
              ? (Math.abs(debt) / totalAssets) * 100
              : 0;
            return (
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">融资欠款</CardTitle>
                  <DollarSign className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className={`text-2xl font-bold ${hasDebt ? "" : "text-muted-foreground"}`}>
                    {formatCurrency(fromHkd(debt), displayCurrency)}
                  </div>
                  <p className="text-xs text-muted-foreground mt-1">
                    {hasDebt ? `占总资产 ${debtPctOfTotal.toFixed(1)}%` : "未使用融资"}
                  </p>
                </CardContent>
              </Card>
            );
          })()}
          </div>
        </>
      )}

      {/* 融资 / 保证金信息 */}
      {account && (account.max_finance_amount > 0 || account.cash_infos?.length) && (
        <FinancingCard account={account} />
      )}

      {!account && !accountQuery.isLoading && (
        <Card>
          <CardContent className="p-8 text-center text-muted-foreground">
            暂无账户数据，请点击右上角"同步数据"按钮
          </CardContent>
        </Card>
      )}

      {/* Positions Tables */}
      {allPositions.length === 0 && !positionsQuery.isLoading && (
        <Card>
          <CardContent className="p-8 text-center text-muted-foreground">
            暂无持仓数据，请点击右上角"同步数据"按钮
          </CardContent>
        </Card>
      )}

      {(hkPositions.length > 0 || hkSoldToday !== 0) && (
        <PositionTable
          title="🇭🇰 港股持仓"
          positions={hkPositions}
          summary={{
            marketValue: hkPositions.reduce((s, p) => s + p.market_value, 0),
            pnl: hkPositions.reduce((s, p) => s + p.unrealized_pnl, 0),
            dayPnl: hkPositions.reduce((s, p) => s + p.day_pnl, 0) + hkSoldToday,
            currency: "HKD",
          }}
          totalPortfolioHkd={totalPortfolioHkd}
          usdToHkd={usdHkd}
          extraDayPnlNote={hkSoldToday !== 0 ? `含今日已平仓 ${formatCurrency(hkSoldToday, "HKD")}` : undefined}
        />
      )}

      {(usStockPositions.length > 0 || usSoldToday !== 0) && (
        <PositionTable
          title="🇺🇸 美股持仓"
          positions={usStockPositions}
          summary={{
            marketValue: usStockPositions.reduce((s, p) => s + p.market_value, 0),
            pnl: usStockPositions.reduce((s, p) => s + p.unrealized_pnl, 0),
            dayPnl: usStockPositions.reduce((s, p) => s + p.day_pnl, 0) + usSoldToday,
            currency: "USD",
          }}
          quoteMap={usQuoteMap}
          totalPortfolioHkd={totalPortfolioHkd}
          usdToHkd={usdHkd}
          extraDayPnlNote={usSoldToday !== 0 ? `含今日已平仓 ${formatCurrency(usSoldToday, "USD")}` : undefined}
        />
      )}

      {usOptionPositions.length > 0 && (
        <PositionTable
          title="🇺🇸 美股期权"
          titleWarning="⚠ 期权敞口需密切关注：到期日、IV 变化、对冲缺口"
          positions={usOptionPositions}
          summary={{
            marketValue: usOptionPositions.reduce((s, p) => s + p.market_value, 0),
            pnl: usOptionPositions.reduce((s, p) => s + p.unrealized_pnl, 0),
            dayPnl: usOptionPositions.reduce((s, p) => s + p.day_pnl, 0),
            currency: "USD",
          }}
          totalPortfolioHkd={totalPortfolioHkd}
          usdToHkd={usdHkd}
        />
      )}
    </div>
  );
}

interface PositionSummary {
  marketValue: number;
  pnl: number;
  dayPnl: number;
  currency: string;
}

type PositionRow = {
  id: number;
  symbol: string;
  name: string;
  quantity: number;
  cost_price: number;
  current_price: number;
  prev_close: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_ratio: number;
  day_pnl: number;
  day_pnl_ratio: number;
  currency: string;
};

function PositionTable({
  title,
  titleWarning,
  positions,
  summary,
  quoteMap,
  totalPortfolioHkd,
  usdToHkd,
  extraDayPnlNote,
}: {
  title: string;
  titleWarning?: string;
  positions: PositionRow[];
  summary: PositionSummary;
  quoteMap?: QuoteMap;
  totalPortfolioHkd: number;
  usdToHkd: number;
  extraDayPnlNote?: string;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <div className="flex flex-col gap-1">
          <CardTitle>{title}</CardTitle>
          {titleWarning && (
            <span className="text-xs text-amber-600 dark:text-amber-500">{titleWarning}</span>
          )}
        </div>
        <div className="flex items-center gap-4 text-sm">
          <span className="text-muted-foreground">
            市值 <span className="font-medium text-foreground">{formatCurrency(summary.marketValue, summary.currency)}</span>
          </span>
          <span className="text-muted-foreground">
            盈亏{" "}
            <span className={`font-medium ${pnlColor(summary.pnl)}`}>
              {formatCurrency(summary.pnl, summary.currency)}
            </span>
          </span>
          <span className="text-muted-foreground">
            今日{" "}
            <span className={`font-medium ${pnlColor(summary.dayPnl)}`}>
              {formatCurrency(summary.dayPnl, summary.currency)}
            </span>
            {extraDayPnlNote && (
              <span className="ml-2 text-xs text-muted-foreground">({extraDayPnlNote})</span>
            )}
          </span>
        </div>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="pb-3 font-medium">标的</th>
                <th className="pb-3 font-medium">名称</th>
                <th className="pb-3 font-medium text-right">数量</th>
                <th className="pb-3 font-medium text-right">成本价</th>
                <th className="pb-3 font-medium text-right">现价</th>
                <th className="pb-3 font-medium text-right">收盘价</th>
                <th className="pb-3 font-medium text-right">市值</th>
                <th className="pb-3 font-medium text-right">占组合</th>
                <th className="pb-3 font-medium text-right">浮动盈亏</th>
                <th className="pb-3 font-medium text-right">盈亏比例</th>
                <th className="pb-3 font-medium text-right">当日盈亏</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((pos) => {
                const quote = quoteMap?.[pos.symbol];
                // 优先使用实时报价（盘前/盘中/盘后），fallback 到 DB 中的快照价
                const livePrice = quote?.current_price && quote.current_price > 0
                  ? quote.current_price
                  : pos.current_price;
                // 用最新价回算市值与浮动盈亏，让用户看到最实时的数据
                const liveMktValue = quote
                  ? Math.abs(pos.quantity) * livePrice
                  : pos.market_value;
                // 集中度 + 杠杆识别
                const hkdValue = pos.currency === "USD" ? liveMktValue * usdToHkd : liveMktValue;
                const rule = evaluatePosition(pos.symbol, pos.name, hkdValue, totalPortfolioHkd);
                const livePnl = quote
                  ? (livePrice - pos.cost_price) * pos.quantity
                  : pos.unrealized_pnl;
                const livePnlRatio = quote && pos.cost_price
                  ? (livePrice - pos.cost_price) / pos.cost_price
                  : pos.unrealized_pnl_ratio;
                // 收盘价：始终使用 DB 快照值（上一交易日 16:00 收盘价）
                const prevClose = pos.prev_close || 0;
                // 当日盈亏：用实时价 - DB 收盘价计算（不用 WebSocket 的 prev_close，
                // 因为它可能是盘后价而非正式收盘价）
                const liveDayPnl = quote && prevClose > 0
                  ? (livePrice - prevClose) * pos.quantity
                  : pos.day_pnl;
                const liveDayPnlRatio = quote && prevClose > 0
                  ? (livePrice - prevClose) / prevClose
                  : pos.day_pnl_ratio;

                return (
                  <tr key={pos.id} className="border-b last:border-0">
                    <td className="py-3 font-medium">{pos.symbol}</td>
                    <td className="py-3 text-muted-foreground">
                      <span>{pos.name}</span>
                      {rule.kindTag && (
                        <span className="ml-1.5 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-500">
                          {rule.kindTag}
                        </span>
                      )}
                    </td>
                    <td className="py-3 text-right">{pos.quantity}</td>
                    <td className="py-3 text-right">{pos.cost_price.toFixed(2)}</td>
                    <td className="py-3 text-right">
                      <PriceCell price={livePrice} quote={quote} prevClose={prevClose} />
                    </td>
                    <td className="py-3 text-right text-muted-foreground">
                      {prevClose > 0 ? prevClose.toFixed(2) : '-'}
                    </td>
                    <td className="py-3 text-right">
                      {formatCurrency(liveMktValue, pos.currency)}
                    </td>
                    <td className={`py-3 text-right font-medium ${rule.warn ? "text-red-600" : "text-muted-foreground"}`}>
                      {(rule.ratio * 100).toFixed(1)}%
                      {rule.warn && <span className="ml-0.5">⚠</span>}
                    </td>
                    <td className={`py-3 text-right font-medium ${pnlColor(livePnl)}`}>
                      {formatCurrency(livePnl, pos.currency)}
                    </td>
                    <td className={`py-3 text-right ${pnlColor(livePnlRatio)}`}>
                      {formatPercent(livePnlRatio * 100)}
                    </td>
                    <td className={`py-3 text-right font-medium ${pnlColor(liveDayPnl)}`}>
                      {formatCurrency(liveDayPnl, pos.currency)}
                      <span className={`text-xs ml-1 ${pnlColor(liveDayPnlRatio)}`}>
                        {formatPercent(liveDayPnlRatio * 100)}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

const SESSION_BADGE: Record<string, { label: string; cls: string }> = {
  pre: { label: "盘前", cls: "bg-amber-100 text-amber-700" },
  post: { label: "盘后", cls: "bg-sky-100 text-sky-700" },
  overnight: { label: "夜盘", cls: "bg-purple-100 text-purple-700" },
};

function PriceCell({ price, quote, prevClose }: { price: number; quote?: QuoteData; prevClose: number }) {
  if (!quote) {
    return <span>{price.toFixed(2)}</span>;
  }

  const session = quote.trading_session;
  const badge = SESSION_BADGE[session];

  // 副标题：在延伸时段（盘前/盘后/夜盘）展示相对正式收盘的偏离
  // price 主显示的是延伸时段成交价（current_price），副标显示其相对 regular 收盘 (last_done) 的变化
  let subline: { value: number; ratio: number } | null = null;
  if (session === "pre" && quote.pre_market_price > 0 && prevClose > 0) {
    const change = quote.pre_market_price - prevClose;
    subline = { value: change, ratio: (change / prevClose) * 100 };
  } else if ((session === "post" || session === "overnight") && quote.post_market_price > 0 && quote.last_done > 0) {
    const change = quote.post_market_price - quote.last_done;
    subline = { value: change, ratio: (change / quote.last_done) * 100 };
  }

  return (
    <div className="flex flex-col items-end leading-tight">
      <div className="flex items-center gap-1.5">
        <span>{price.toFixed(2)}</span>
        {badge && (
          <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${badge.cls}`}>
            {badge.label}
          </span>
        )}
      </div>
      {subline && (
        <span className={`text-[11px] ${pnlColor(subline.value)}`}>
          {subline.value >= 0 ? "+" : ""}
          {subline.value.toFixed(2)} ({formatPercent(subline.ratio)})
        </span>
      )}
    </div>
  );
}

type WsStatus = "connecting" | "connected" | "reconnecting" | "closed";

function WsStatusBadge({ status }: { status: WsStatus }) {
  const config: Record<WsStatus, { icon: ReactNode; label: string; cls: string }> = {
    connected: {
      icon: <Wifi className="h-3 w-3" />,
      label: "实时",
      cls: "text-emerald-600",
    },
    connecting: {
      icon: <RotateCcw className="h-3 w-3 animate-spin" />,
      label: "连接中",
      cls: "text-amber-500",
    },
    reconnecting: {
      icon: <RotateCcw className="h-3 w-3 animate-spin" />,
      label: "重连中",
      cls: "text-amber-500",
    },
    closed: {
      icon: <WifiOff className="h-3 w-3" />,
      label: "未连接",
      cls: "text-muted-foreground",
    },
  };

  const { icon, label, cls } = config[status];

  return (
    <span className={`inline-flex items-center gap-1 text-xs ${cls}`}>
      {icon}
      {label}
    </span>
  );
}

type AccountData = NonNullable<Awaited<ReturnType<typeof getAccount>>["data"]>;

function FinancingCard({ account }: { account: AccountData }) {
  const marginUtilization = account.maintenance_margin > 0 && account.net_assets > 0
    ? (account.maintenance_margin / account.net_assets) * 100
    : 0;
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">保证金 / 现金明细</CardTitle>
        {account.margin_call ? (
          <span className="text-xs font-medium text-red-600">⚠ 触发追保</span>
        ) : (
          <span className="text-xs text-muted-foreground">风险等级 {account.risk_level}</span>
        )}
      </CardHeader>
      <CardContent>
        <div className="grid gap-4 md:grid-cols-3 text-sm">
          {/* 保证金占用 */}
          <div>
            <div className="text-xs text-muted-foreground mb-1">维持保证金 / 净资产</div>
            <div className={`text-lg font-bold ${marginUtilization > 80 ? "text-red-600" : marginUtilization > 60 ? "text-amber-600" : ""}`}>
              {marginUtilization.toFixed(1)}%
            </div>
            <div className="text-xs text-muted-foreground mt-0.5">
              维 {formatCurrency(account.maintenance_margin, account.currency)}
              {" · "}
              初 {formatCurrency(account.init_margin, account.currency)}
            </div>
          </div>

          {/* 购买力 */}
          <div>
            <div className="text-xs text-muted-foreground mb-1">可用购买力</div>
            <div className="text-lg font-bold">{formatCurrency(account.buy_power, account.currency)}</div>
            <div className="text-xs text-muted-foreground mt-0.5">剩余可融 {formatCurrency(account.remaining_finance_amount, account.currency)}</div>
          </div>

          {/* 融资额度概览 */}
          <div>
            <div className="text-xs text-muted-foreground mb-1">融资总额度</div>
            <div className="text-lg font-bold">{formatCurrency(account.max_finance_amount, account.currency)}</div>
            <div className="text-xs text-muted-foreground mt-0.5">
              已用 {formatCurrency(account.max_finance_amount - account.remaining_finance_amount, account.currency)}
            </div>
          </div>
        </div>

        {/* 按币种现金明细 */}
        {account.cash_infos?.length > 0 && (
          <div className="mt-4 pt-3 border-t">
            <div className="text-xs text-muted-foreground mb-2">现金明细（按币种）</div>
            <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4 text-xs">
              {account.cash_infos.map((ci) => (
                <div key={ci.currency} className="flex flex-col">
                  <div className="flex justify-between">
                    <span className="font-medium">{ci.currency}</span>
                    <span className={ci.available < 0 ? "font-medium text-red-600" : "font-medium"}>
                      {formatCurrency(ci.available, ci.currency)}
                    </span>
                  </div>
                  <div className="text-muted-foreground mt-0.5">
                    {ci.available < 0 ? "账户透支借款" : "可用"}
                    {ci.frozen > 0 && (
                      <span className="ml-1.5">· 冻结 {formatCurrency(ci.frozen, ci.currency)}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
