import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { getPnlSummary } from "@/api/client";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { formatCurrency, pnlColor } from "@/lib/utils";

export default function PnL() {
  const [groupBy, setGroupBy] = useState<"symbol" | "market">("symbol");

  const query = useQuery({
    queryKey: ["pnl", groupBy],
    queryFn: () => getPnlSummary(groupBy),
  });

  const items = query.data?.data ?? [];

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold tracking-tight">盈亏分析</h2>
        <div className="flex gap-2">
          <Button
            variant={groupBy === "symbol" ? "default" : "outline"}
            size="sm"
            onClick={() => setGroupBy("symbol")}
          >
            按标的
          </Button>
          <Button
            variant={groupBy === "market" ? "default" : "outline"}
            size="sm"
            onClick={() => setGroupBy("market")}
          >
            按市场
          </Button>
        </div>
      </div>

      {/* Chart */}
      {items.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>盈亏分布</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={items} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="group" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} />
                <Tooltip
                  formatter={(value: number) => formatCurrency(value)}
                  labelFormatter={(label: string) => `标的: ${label}`}
                />
                <Bar dataKey="total_pnl" name="盈亏">
                  {items.map((entry, index) => (
                    <Cell
                      key={`cell-${index}`}
                      fill={entry.total_pnl >= 0 ? "#16a34a" : "#dc2626"}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      )}

      {/* Table */}
      <Card>
        <CardHeader>
          <CardTitle>盈亏明细</CardTitle>
        </CardHeader>
        <CardContent>
          {items.length === 0 ? (
            <p className="text-center text-muted-foreground py-4">暂无盈亏数据</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="pb-3 font-medium">{groupBy === "symbol" ? "标的" : "市场"}</th>
                    <th className="pb-3 font-medium text-right">市值</th>
                    <th className="pb-3 font-medium text-right">成本</th>
                    <th className="pb-3 font-medium text-right">未实现盈亏</th>
                    <th className="pb-3 font-medium text-right">总盈亏</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.group} className="border-b last:border-0">
                      <td className="py-3 font-medium">{item.group}</td>
                      <td className="py-3 text-right">{formatCurrency(item.market_value)}</td>
                      <td className="py-3 text-right">{formatCurrency(item.cost_value)}</td>
                      <td className={`py-3 text-right ${pnlColor(item.unrealized_pnl)}`}>
                        {formatCurrency(item.unrealized_pnl)}
                      </td>
                      <td className={`py-3 text-right font-medium ${pnlColor(item.total_pnl)}`}>
                        {formatCurrency(item.total_pnl)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
