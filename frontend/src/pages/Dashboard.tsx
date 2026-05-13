import { useEffect, useMemo, useRef, type ReactNode } from "react";
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
import { useQuoteWebSocket } from "@/hooks/useQuoteWebSocket";
import { formatCurrency, formatPercent, pnlColor } from "@/lib/utils";

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
  // 美股 USD → HKD 用与后端一致的 fallback 汇率（实际汇率由后端同步时写入账户快照）
  const USD_TO_HKD_FALLBACK = 7.83;
  const liveTotalPnl = useMemo(() => {
    if (!account) return null;
    // 港股持仓：用 DB 快照（港股无盘前盘后，快照即最新）
    const hkPnl = hkPositions.reduce((sum, pos) => sum + pos.unrealized_pnl, 0);
    // 美股正股：用实时价回算
    const usPnl = usStockPositions.reduce((sum, pos) => {
      const quote = usQuoteMap[pos.symbol];
      const livePrice = quote?.current_price && quote.current_price > 0
        ? quote.current_price
        : pos.current_price;
      const pnl = (livePrice - pos.cost_price) * pos.quantity;
      return sum + pnl * USD_TO_HKD_FALLBACK;
    }, 0);
    // 期权：用 DB 快照（期权无盘前盘后报价）
    const optionPnl = usOptionPositions.reduce(
      (sum, pos) => sum + pos.unrealized_pnl * USD_TO_HKD_FALLBACK,
      0
    );
    return hkPnl + usPnl + optionPnl;
  }, [hkPositions, usStockPositions, usOptionPositions, usQuoteMap, account]);

  const liveDayPnl = useMemo(() => {
    if (!account) return null;
    const hkDayPnl = hkPositions.reduce((sum, pos) => sum + pos.day_pnl, 0);
    const usDayPnl = usStockPositions.reduce((sum, pos) => {
      const quote = usQuoteMap[pos.symbol];
      const livePrice = quote?.current_price && quote.current_price > 0
        ? quote.current_price
        : pos.current_price;
      const prevClose = pos.prev_close ?? 0;
      const dayPnl = prevClose > 0 ? (livePrice - prevClose) * pos.quantity : pos.day_pnl;
      return sum + dayPnl * USD_TO_HKD_FALLBACK;
    }, 0);
    const optionDayPnl = usOptionPositions.reduce(
      (sum, pos) => sum + pos.day_pnl * USD_TO_HKD_FALLBACK,
      0
    );
    return hkDayPnl + usDayPnl + optionDayPnl;
  }, [hkPositions, usStockPositions, usOptionPositions, usQuoteMap, account]);

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

      {/* Account Cards */}
      {account && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">净资产</CardTitle>
              <Wallet className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {formatCurrency(account.net_assets, account.currency)}
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
                {formatCurrency(account.market_value, account.currency)}
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
                {formatCurrency(account.total_cash, account.currency)}
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
                {formatCurrency(liveTotalPnl ?? account.total_pnl, account.currency)}
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
                {formatCurrency(liveDayPnl ?? account.day_pnl, account.currency)}
              </div>
              {liveDayPnl !== null && liveDayPnl !== account.day_pnl && (
                <p className="text-xs text-muted-foreground mt-1">实时估算</p>
              )}
            </CardContent>
          </Card>
        </div>
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

      {hkPositions.length > 0 && (
        <PositionTable
          title="🇭🇰 港股持仓"
          positions={hkPositions}
          summary={{
            marketValue: hkPositions.reduce((s, p) => s + p.market_value, 0),
            pnl: hkPositions.reduce((s, p) => s + p.unrealized_pnl, 0),
            dayPnl: hkPositions.reduce((s, p) => s + p.day_pnl, 0),
            currency: "HKD",
          }}
        />
      )}

      {usStockPositions.length > 0 && (
        <PositionTable
          title="🇺🇸 美股持仓"
          positions={usStockPositions}
          summary={{
            marketValue: usStockPositions.reduce((s, p) => s + p.market_value, 0),
            pnl: usStockPositions.reduce((s, p) => s + p.unrealized_pnl, 0),
            dayPnl: usStockPositions.reduce((s, p) => s + p.day_pnl, 0),
            currency: "USD",
          }}
          quoteMap={usQuoteMap}
        />
      )}

      {usOptionPositions.length > 0 && (
        <PositionTable
          title="🇺🇸 美股期权"
          positions={usOptionPositions}
          summary={{
            marketValue: usOptionPositions.reduce((s, p) => s + p.market_value, 0),
            pnl: usOptionPositions.reduce((s, p) => s + p.unrealized_pnl, 0),
            dayPnl: usOptionPositions.reduce((s, p) => s + p.day_pnl, 0),
            currency: "USD",
          }}
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
  positions,
  summary,
  quoteMap,
}: {
  title: string;
  positions: PositionRow[];
  summary: PositionSummary;
  quoteMap?: QuoteMap;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>{title}</CardTitle>
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
                    <td className="py-3 text-muted-foreground">{pos.name}</td>
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
};

function PriceCell({ price, quote, prevClose }: { price: number; quote?: QuoteData; prevClose: number }) {
  if (!quote) {
    return <span>{price.toFixed(2)}</span>;
  }

  const session = quote.trading_session;
  const badge = SESSION_BADGE[session];

  // 副标题：盘前/盘后时显示对应的涨跌（用正确的收盘价重新计算）
  let subline: { value: number; ratio: number } | null = null;
  if (session === "pre" && quote.pre_market_price > 0 && prevClose > 0) {
    const change = quote.pre_market_price - prevClose;
    subline = { value: change, ratio: (change / prevClose) * 100 };
  } else if (session === "post" && quote.post_market_price > 0 && prevClose > 0) {
    const change = quote.post_market_price - prevClose;
    subline = { value: change, ratio: (change / prevClose) * 100 };
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
