import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getExecutions } from "@/api/client";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { formatCurrency } from "@/lib/utils";

export default function Executions() {
  const [symbol, setSymbol] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [page, setPage] = useState(1);

  const query = useQuery({
    queryKey: ["executions", symbol, fromDate, toDate, page],
    queryFn: () =>
      getExecutions({
        symbol: symbol || undefined,
        from: fromDate || undefined,
        to: toDate || undefined,
        page,
        size: 50,
      }),
  });

  const data = query.data?.data;
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / 50);

  return (
    <div className="p-6 space-y-6">
      <h2 className="text-2xl font-bold tracking-tight">成交记录</h2>

      {/* Filters */}
      <Card>
        <CardContent className="p-4">
          <div className="flex flex-wrap gap-4 items-end">
            <div>
              <label className="text-sm text-muted-foreground block mb-1">标的</label>
              <input
                type="text"
                placeholder="如 700.HK"
                value={symbol}
                onChange={(e) => { setSymbol(e.target.value); setPage(1); }}
                className="h-9 rounded-md border border-input bg-background px-3 text-sm"
              />
            </div>
            <div>
              <label className="text-sm text-muted-foreground block mb-1">开始日期</label>
              <input
                type="date"
                value={fromDate}
                onChange={(e) => { setFromDate(e.target.value); setPage(1); }}
                className="h-9 rounded-md border border-input bg-background px-3 text-sm"
              />
            </div>
            <div>
              <label className="text-sm text-muted-foreground block mb-1">结束日期</label>
              <input
                type="date"
                value={toDate}
                onChange={(e) => { setToDate(e.target.value); setPage(1); }}
                className="h-9 rounded-md border border-input bg-background px-3 text-sm"
              />
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => { setSymbol(""); setFromDate(""); setToDate(""); setPage(1); }}
            >
              清除
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Table */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>
            共 {total} 条记录
          </CardTitle>
        </CardHeader>
        <CardContent>
          {items.length === 0 ? (
            <p className="text-center text-muted-foreground py-4">暂无成交记录</p>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-3 font-medium">成交时间</th>
                      <th className="pb-3 font-medium">标的</th>
                      <th className="pb-3 font-medium">方向</th>
                      <th className="pb-3 font-medium text-right">价格</th>
                      <th className="pb-3 font-medium text-right">数量</th>
                      <th className="pb-3 font-medium text-right">金额</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((exe) => (
                      <tr key={exe.execution_id} className="border-b last:border-0">
                        <td className="py-3 text-muted-foreground">
                          {new Date(exe.trade_done_at).toLocaleString("zh-CN")}
                        </td>
                        <td className="py-3 font-medium">{exe.symbol}</td>
                        <td className="py-3">
                          <span
                            className={`px-2 py-0.5 rounded text-xs font-medium ${
                              exe.side.toLowerCase().includes("buy")
                                ? "bg-red-100 text-red-700"
                                : "bg-green-100 text-green-700"
                            }`}
                          >
                            {exe.side}
                          </span>
                        </td>
                        <td className="py-3 text-right">{exe.price.toFixed(2)}</td>
                        <td className="py-3 text-right">{exe.quantity}</td>
                        <td className="py-3 text-right">
                          {formatCurrency(exe.price * exe.quantity, exe.currency)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-center gap-2 mt-4">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page <= 1}
                    onClick={() => setPage((p) => p - 1)}
                  >
                    上一页
                  </Button>
                  <span className="text-sm text-muted-foreground">
                    {page} / {totalPages}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page >= totalPages}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    下一页
                  </Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
