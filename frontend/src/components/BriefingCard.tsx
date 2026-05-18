import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, RefreshCw, Sparkles, ExternalLink } from "lucide-react";
import { getBriefing, type BriefingContextItem } from "@/api/client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

const COLLAPSE_KEY = "briefing.collapsed";

export default function BriefingCard() {
  const queryClient = useQueryClient();
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  });

  useEffect(() => {
    localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  const query = useQuery({
    queryKey: ["briefing"],
    queryFn: () => getBriefing(false),
    staleTime: 10 * 60 * 1000,
  });

  const refreshMutation = useMutation({
    mutationFn: () => getBriefing(true),
    onSuccess: (data) => {
      queryClient.setQueryData(["briefing"], data);
    },
  });

  const data = query.data?.data;
  const isLoading = query.isLoading || refreshMutation.isPending;

  return (
    <Card className="border-blue-500/30">
      <CardHeader
        className="flex flex-row items-center justify-between space-y-0 cursor-pointer select-none"
        onClick={() => setCollapsed((v) => !v)}
      >
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-blue-600" />
          <CardTitle className="text-base">今日 AI 复盘</CardTitle>
          {data && (
            <span className="text-xs text-muted-foreground ml-2">
              {data.cache_hit ? "缓存" : "新鲜出炉"} · {formatRelativeTime(data.generated_at)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={(e) => {
              e.stopPropagation();
              refreshMutation.mutate();
            }}
            disabled={isLoading}
          >
            <RefreshCw className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
          </Button>
          {collapsed ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
        </div>
      </CardHeader>

      {!collapsed && (
        <CardContent className="space-y-4">
          {query.isLoading && !data && (
            <div className="space-y-2">
              <div className="h-4 w-3/4 bg-muted rounded animate-pulse" />
              <div className="h-4 w-2/3 bg-muted rounded animate-pulse" />
              <div className="h-4 w-1/2 bg-muted rounded animate-pulse" />
              <p className="text-xs text-muted-foreground pt-2">AI 复盘生成中，首屏约 5-10 秒…</p>
            </div>
          )}

          {query.isError && (
            <div className="text-sm text-red-600">复盘加载失败：{String(query.error)}</div>
          )}

          {data && (
            <>
              {/* 大盘背景指标 */}
              {Object.keys(data.context).length > 0 && (
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs pb-2 border-b">
                  {Object.entries(data.context).map(([sym, ctx]) => (
                    <ContextChip key={sym} symbol={sym} ctx={ctx} />
                  ))}
                </div>
              )}

              {/* 大盘总结 */}
              {data.market_summary && (
                <div>
                  <div className="text-xs font-medium text-muted-foreground mb-1">📊 今日盘面</div>
                  <p className="text-sm leading-relaxed">{data.market_summary}</p>
                </div>
              )}

              {/* 每只重仓 */}
              {data.stocks.length > 0 && (
                <div className="space-y-3">
                  <div className="text-xs font-medium text-muted-foreground">🎯 重仓股复盘</div>
                  {data.stocks.map((s) => (
                    <div key={s.symbol} className="rounded-md border p-3 space-y-2">
                      <div className="font-semibold text-sm">{s.symbol}</div>

                      {s.headlines.length > 0 && (
                        <div className="space-y-1">
                          {s.headlines.map((h, i) => (
                            <a
                              key={i}
                              href={h.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="block text-xs text-blue-600 hover:underline truncate"
                              title={h.title}
                            >
                              <ExternalLink className="inline h-3 w-3 mr-1" />
                              {h.title}
                            </a>
                          ))}
                        </div>
                      )}

                      <div className="grid gap-1.5 text-xs">
                        <div>
                          <span className="text-green-600 font-medium">✅ 利好：</span>
                          <span>{s.bullish}</span>
                        </div>
                        <div>
                          <span className="text-amber-600 font-medium">⚠️ 利空：</span>
                          <span>{s.bearish}</span>
                        </div>
                        <div>
                          <span className="text-blue-600 font-medium">💡 建议：</span>
                          <span>{s.suggestion}</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* 总体建议 */}
              {data.overall_action && (
                <div className="rounded-md bg-blue-50 dark:bg-blue-950/20 p-3 text-sm">
                  <div className="text-xs font-medium text-blue-700 dark:text-blue-400 mb-1">
                    🧭 总体操作建议
                  </div>
                  {data.overall_action}
                </div>
              )}

              <p className="text-[10px] text-muted-foreground pt-2 border-t">
                以上由 AI 基于公开新闻与持仓数据生成，仅供个人参考，不构成投资建议。
              </p>
            </>
          )}
        </CardContent>
      )}
    </Card>
  );
}

function ContextChip({ symbol, ctx }: { symbol: string; ctx: BriefingContextItem }) {
  const label = CONTEXT_LABELS[symbol] ?? ctx.name ?? symbol;
  const pct = ctx.change_percent ?? 0;
  const cls = pct > 0 ? "text-red-600" : pct < 0 ? "text-green-600" : "text-muted-foreground";
  return (
    <span className="inline-flex items-center gap-1">
      <span className="text-muted-foreground">{label}</span>
      <span className={`font-medium ${cls}`}>
        {ctx.price != null ? ctx.price.toFixed(2) : "—"}
        {pct !== 0 && (
          <span className="ml-0.5">({pct > 0 ? "+" : ""}{pct.toFixed(2)}%)</span>
        )}
      </span>
    </span>
  );
}

const CONTEXT_LABELS: Record<string, string> = {
  "^GSPC": "标普500",
  "ES=F": "标普期货",
  "^IXIC": "纳指",
  "NQ=F": "纳指期货",
  "CL=F": "原油",
  "^HSI": "恒指",
};

function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} 小时前`;
  return new Date(iso).toLocaleString("zh-CN");
}
