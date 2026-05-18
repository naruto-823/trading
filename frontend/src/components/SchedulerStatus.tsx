import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, ChevronDown, ChevronUp, Play, AlertCircle, CheckCircle2 } from "lucide-react";
import { listJobs, runJob, type JobStatus } from "@/api/client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

function relTime(iso: string | null): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const abs = Math.abs(diff);
  const mins = Math.floor(abs / 60000);
  const prefix = diff >= 0 ? "" : "在 ";
  const suffix = diff >= 0 ? " 前" : " 后";
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${prefix}${mins} 分钟${suffix}`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${prefix}${hrs} 小时${suffix}`;
  const days = Math.floor(hrs / 24);
  return `${prefix}${days} 天${suffix}`;
}

function statusBadge(s: JobStatus["last_status"]) {
  if (s === "success") return { icon: <CheckCircle2 className="h-3 w-3 text-green-600" />, label: "成功", cls: "text-green-600" };
  if (s === "error") return { icon: <AlertCircle className="h-3 w-3 text-red-600" />, label: "失败", cls: "text-red-600" };
  return { icon: <Clock className="h-3 w-3 text-muted-foreground" />, label: "未运行", cls: "text-muted-foreground" };
}

export default function SchedulerStatus() {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ["jobs"],
    queryFn: () => listJobs(),
    refetchInterval: open ? 5000 : 60000,
    staleTime: 4000,
  });

  const runMutation = useMutation({
    mutationFn: (id: string) => runJob(id),
    onSuccess: () => {
      // 给 worker 一点时间跑完再 refresh 状态
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ["jobs"] }), 1500);
    },
  });

  const jobs = query.data?.data ?? [];
  const errors = jobs.filter((j) => j.last_status === "error").length;
  const recent = jobs
    .filter((j) => j.last_run_at)
    .sort((a, b) => new Date(b.last_run_at!).getTime() - new Date(a.last_run_at!).getTime())[0];

  return (
    <Card>
      <CardHeader
        className="cursor-pointer select-none pb-3"
        onClick={() => setOpen((v) => !v)}
      >
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Clock className="h-4 w-4 text-blue-600" />
            后台调度
            <span className="text-xs font-normal text-muted-foreground">
              {jobs.length} jobs
              {recent && ` · 上次运行：${relTime(recent.last_run_at)}`}
              {errors > 0 && <span className="text-red-600 ml-1">· {errors} 个失败</span>}
            </span>
          </span>
          {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </CardTitle>
      </CardHeader>
      {open && (
        <CardContent>
          <div className="space-y-2">
            {jobs.map((j) => {
              const sb = statusBadge(j.last_status);
              return (
                <div key={j.id} className="flex items-center gap-3 text-xs border-b last:border-0 pb-2 last:pb-0">
                  <div className="flex-1 min-w-0">
                    <div className="font-medium truncate">{j.name}</div>
                    <div className="text-muted-foreground text-[10px] truncate">
                      <span className="font-mono">{j.id}</span> · {j.trigger}
                    </div>
                  </div>
                  <div className="flex flex-col items-end text-[10px] w-32 text-muted-foreground">
                    <div className="flex items-center gap-1">
                      {sb.icon}
                      <span className={sb.cls}>{sb.label}</span>
                      {j.last_duration_ms != null && (
                        <span>· {(j.last_duration_ms / 1000).toFixed(1)}s</span>
                      )}
                    </div>
                    <div>上次：{relTime(j.last_run_at)}</div>
                    <div>下次：{relTime(j.next_run_at)}</div>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-6 text-xs px-2"
                    onClick={() => runMutation.mutate(j.id)}
                    disabled={runMutation.isPending}
                  >
                    <Play className="h-3 w-3 mr-1" />
                    立即跑
                  </Button>
                </div>
              );
            })}
          </div>
          {jobs.some((j) => j.last_error) && (
            <div className="mt-3 text-xs text-red-600 space-y-1 border-t pt-2">
              {jobs.filter((j) => j.last_error).map((j) => (
                <div key={j.id}>
                  <span className="font-mono">{j.id}</span>: {j.last_error}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      )}
    </Card>
  );
}
