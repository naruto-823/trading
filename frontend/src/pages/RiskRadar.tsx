import { useQuery } from "@tanstack/react-query";
import { ShieldAlert, TriangleAlert } from "lucide-react";
import { getRiskRadar } from "@/api/client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatCurrency } from "@/lib/utils";

const levelClass = { normal: "text-emerald-600", medium: "text-amber-600", high: "text-red-600" };

export default function RiskRadar() {
  const query = useQuery({ queryKey: ["riskRadar"], queryFn: () => getRiskRadar(10) });
  const data = query.data?.data;
  if (query.isLoading) return <div className="p-8 text-muted-foreground">正在计算组合风险…</div>;
  if (!data) return <div className="p-8 text-muted-foreground">暂无账户数据，请先同步。</div>;

  return <div className="p-6 max-w-6xl mx-auto space-y-5">
    <div>
      <h2 className="text-2xl font-bold">风险雷达</h2>
      <p className="text-sm text-muted-foreground mt-1">基于真实账户快照的确定性计算，不调用 AI。股票压力测试：{data.stress?.shock_pct}%</p>
    </div>
    <div className="grid gap-3 md:grid-cols-2">
      {data.summary.map((item) => <Card key={item.title}><CardContent className="pt-5 flex gap-3"><ShieldAlert className={levelClass[item.level]} /><div><div className={`font-semibold ${levelClass[item.level]}`}>{item.title}</div><div className="text-sm text-muted-foreground mt-1">{item.detail}</div></div></CardContent></Card>)}
    </div>
    {data.stress && <Card><CardHeader><CardTitle>股票压力测试</CardTitle></CardHeader><CardContent><div className="text-2xl font-bold text-red-600">{formatCurrency(data.stress.estimated_stock_pnl_hkd, "HKD")}</div><div className="text-sm text-muted-foreground mt-1">若现有正股同步下跌 {Math.abs(data.stress.shock_pct)}%，预计净资产变为 {formatCurrency(data.stress.estimated_net_assets_hkd, "HKD")}。</div><p className="text-xs text-muted-foreground mt-3">{data.stress.excluded}</p></CardContent></Card>}
    <Card><CardHeader><CardTitle>集中度</CardTitle></CardHeader><CardContent className="space-y-3">{data.concentration.slice(0, 10).map((item) => <div key={item.symbol} className="flex items-center gap-3"><div className="w-20 font-medium">{item.symbol}</div><div className="flex-1 h-2 rounded bg-muted overflow-hidden"><div className={`h-full ${item.risk_level === "high" ? "bg-red-500" : item.risk_level === "medium" ? "bg-amber-500" : "bg-blue-500"}`} style={{ width: `${Math.min(item.weight_pct, 100)}%` }} /></div><div className="w-16 text-right font-medium">{item.weight_pct.toFixed(1)}%</div><div className="w-28 text-right text-sm text-muted-foreground">{formatCurrency(item.market_value_hkd, "HKD")}</div></div>)}</CardContent></Card>
    <Card><CardHeader><CardTitle>期权生命周期</CardTitle></CardHeader><CardContent className="space-y-3">{data.options.length === 0 ? <div className="text-sm text-muted-foreground">当前没有已识别的期权仓位。</div> : data.options.map((item) => <div key={item.symbol} className="rounded border p-3"><div className="flex justify-between gap-3"><div className="font-medium">{item.symbol} · {item.kind === "call" ? "Call" : "Put"} ${item.strike}</div><div className="text-sm">到期 {item.days_to_expiry} 天</div></div><div className="text-sm text-muted-foreground mt-1">{item.quantity} 张 · {item.expiry}{item.quantity < 0 && item.kind === "call" ? ` · ${item.covered ? "已覆盖" : "未覆盖"}` : ""}</div>{item.risk_flags.map((flag) => <div key={flag} className="mt-2 text-sm text-amber-700 flex gap-1"><TriangleAlert className="w-4 h-4 mt-0.5" />{flag}</div>)}</div>)}</CardContent></Card>
  </div>;
}
