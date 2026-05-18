import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, BellOff, Plus, Trash2, RefreshCw, Send, AlertCircle, CheckCircle2 } from "lucide-react";
import {
  createAlert,
  deleteAlert,
  getNotifyStatus,
  listAlerts,
  testNotify,
  updateAlert,
  type AlertApi,
  type AlertCondition,
  type AlertCreatePayload,
} from "@/api/client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

const CONDITION_LABEL: Record<AlertCondition, string> = {
  price_above: "现价突破（>）",
  price_below: "现价跌破（<）",
  day_change_pct_above: "日内涨幅突破（+%）",
  day_change_pct_below: "日内跌幅突破（-%）",
};

const CONDITION_UNIT: Record<AlertCondition, string> = {
  price_above: "$",
  price_below: "$",
  day_change_pct_above: "%",
  day_change_pct_below: "%",
};

function relTime(ms: number | null): string {
  if (!ms) return "—";
  const diff = Date.now() - ms;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} 小时前`;
  return new Date(ms).toLocaleString("zh-CN");
}

export default function Alerts() {
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);

  const alertsQuery = useQuery({
    queryKey: ["alerts"],
    queryFn: () => listAlerts(),
  });
  const notifyStatusQuery = useQuery({
    queryKey: ["notifyStatus"],
    queryFn: () => getNotifyStatus(),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["alerts"] });

  const createMutation = useMutation({
    mutationFn: (p: AlertCreatePayload) => createAlert(p),
    onSuccess: () => {
      invalidate();
      setShowForm(false);
    },
  });
  const updateMutation = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Partial<AlertCreatePayload> & { reset_cooldown?: boolean } }) =>
      updateAlert(id, patch),
    onSuccess: invalidate,
  });
  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteAlert(id),
    onSuccess: invalidate,
  });
  const testMutation = useMutation({
    mutationFn: () => testNotify(),
  });

  const alerts = alertsQuery.data?.data ?? [];
  const notifyConfigured = notifyStatusQuery.data?.data?.configured ?? false;

  return (
    <div className="p-6 space-y-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">价格告警</h2>
          <p className="text-sm text-muted-foreground mt-1">
            条件命中 → Bark iOS 推送 · market-watcher 每 60s 检查一次
          </p>
        </div>
        <Button onClick={() => setShowForm((v) => !v)}>
          <Plus className="h-4 w-4 mr-1" />
          新建规则
        </Button>
      </div>

      {/* Telegram 状态 */}
      <Card className={notifyConfigured ? "border-green-500/40" : "border-amber-500/40"}>
        <CardContent className="py-4 flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-2 text-sm">
            {notifyConfigured ? (
              <>
                <CheckCircle2 className="h-4 w-4 text-green-600" />
                <span>Bark 已配置（iOS 推送）</span>
              </>
            ) : (
              <>
                <AlertCircle className="h-4 w-4 text-amber-600" />
                <span>
                  Bark 未配置 —— 在 .env 加 <code className="font-mono">BARK_DEVICE_KEY</code>
                  （在 Bark app 首页复制）
                </span>
              </>
            )}
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={() => testMutation.mutate()}
            disabled={!notifyConfigured || testMutation.isPending}
          >
            <Send className="h-3 w-3 mr-1" />
            发测试消息
          </Button>
        </CardContent>
        {testMutation.data && testMutation.data.data?.ok && (
          <CardContent className="pt-0 pb-3 text-xs text-green-700">✓ 已发送，看 Telegram</CardContent>
        )}
        {testMutation.isError && (
          <CardContent className="pt-0 pb-3 text-xs text-red-600">
            发送失败：{String(testMutation.error)}
          </CardContent>
        )}
      </Card>

      {showForm && <AlertForm onSubmit={(p) => createMutation.mutate(p)} onCancel={() => setShowForm(false)} />}

      {/* 规则列表 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">规则列表（{alerts.length}）</CardTitle>
        </CardHeader>
        <CardContent>
          {alerts.length === 0 ? (
            <p className="text-sm text-muted-foreground">还没有规则。点右上"新建规则"加一条。</p>
          ) : (
            <div className="space-y-2">
              {alerts.map((a) => (
                <AlertRow
                  key={a.id}
                  alert={a}
                  onToggle={(enabled) => updateMutation.mutate({ id: a.id, patch: { enabled } })}
                  onResetCooldown={() => updateMutation.mutate({ id: a.id, patch: { reset_cooldown: true } })}
                  onDelete={() => {
                    if (confirm("删除这条规则？")) deleteMutation.mutate(a.id);
                  }}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function AlertRow({
  alert,
  onToggle,
  onResetCooldown,
  onDelete,
}: {
  alert: AlertApi;
  onToggle: (enabled: boolean) => void;
  onResetCooldown: () => void;
  onDelete: () => void;
}) {
  const unit = CONDITION_UNIT[alert.condition];
  const condLabel = CONDITION_LABEL[alert.condition];
  return (
    <div className={`rounded-md border p-3 flex items-center gap-3 flex-wrap ${alert.enabled ? "" : "opacity-60"}`}>
      <button
        onClick={() => onToggle(!alert.enabled)}
        className="flex-shrink-0"
        title={alert.enabled ? "点击禁用" : "点击启用"}
      >
        {alert.enabled ? (
          <Bell className="h-5 w-5 text-blue-600" />
        ) : (
          <BellOff className="h-5 w-5 text-muted-foreground" />
        )}
      </button>

      <div className="flex-1 min-w-0">
        <div className="font-mono font-semibold text-sm">{alert.symbol}</div>
        <div className="text-xs text-muted-foreground">
          {condLabel} <span className="font-mono">{unit}{alert.threshold}</span>
          {" · "}冷却 {alert.cooldown_minutes}min
          {" · "}已触发 {alert.trigger_count} 次
        </div>
        {alert.note && <div className="text-xs italic text-foreground/80 mt-0.5">{alert.note}</div>}
      </div>

      <div className="text-xs text-muted-foreground text-right">
        {alert.last_triggered_at_ms ? (
          <>上次触发 {relTime(alert.last_triggered_at_ms)}</>
        ) : (
          <>从未触发</>
        )}
      </div>

      <Button
        size="sm"
        variant="ghost"
        onClick={onResetCooldown}
        disabled={!alert.last_triggered_at_ms}
        title="重置冷却期，让下次命中能立刻推送"
        className="h-7 text-xs"
      >
        <RefreshCw className="h-3 w-3" />
      </Button>
      <Button size="sm" variant="ghost" onClick={onDelete} className="h-7 text-xs">
        <Trash2 className="h-3 w-3" />
      </Button>
    </div>
  );
}

function AlertForm({
  onSubmit,
  onCancel,
}: {
  onSubmit: (p: AlertCreatePayload) => void;
  onCancel: () => void;
}) {
  const [symbol, setSymbol] = useState("");
  const [condition, setCondition] = useState<AlertCondition>("price_below");
  const [threshold, setThreshold] = useState("");
  const [note, setNote] = useState("");
  const [cooldown, setCooldown] = useState("60");

  const canSubmit = symbol.trim() && threshold.trim() && !isNaN(parseFloat(threshold));

  const submit = () => {
    if (!canSubmit) return;
    onSubmit({
      symbol: symbol.trim().toUpperCase(),
      condition,
      threshold: parseFloat(threshold),
      note: note.trim(),
      cooldown_minutes: parseInt(cooldown) || 60,
    });
  };

  return (
    <Card className="border-blue-500/30">
      <CardHeader>
        <CardTitle className="text-base">新建告警规则</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <Field label="标的（必须 .US，HK 暂不支持实时监控）">
            <input
              className="w-full rounded-md border px-2 py-1.5 text-sm bg-background font-mono"
              placeholder="META.US"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
            />
          </Field>
          <Field label="条件">
            <select
              className="w-full rounded-md border px-2 py-1.5 text-sm bg-background"
              value={condition}
              onChange={(e) => setCondition(e.target.value as AlertCondition)}
            >
              {(Object.entries(CONDITION_LABEL) as [AlertCondition, string][]).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </Field>
          <Field label={`阈值（${CONDITION_UNIT[condition]}）`}>
            <input
              className="w-full rounded-md border px-2 py-1.5 text-sm bg-background"
              placeholder={CONDITION_UNIT[condition] === "$" ? "590" : "-3"}
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
            />
          </Field>
          <Field label="冷却期（分钟）">
            <input
              className="w-full rounded-md border px-2 py-1.5 text-sm bg-background"
              placeholder="60"
              value={cooldown}
              onChange={(e) => setCooldown(e.target.value)}
            />
          </Field>
        </div>
        <Field label="备注（推送消息里会显示）">
          <input
            className="w-full rounded-md border px-2 py-1.5 text-sm bg-background"
            placeholder="如：META 关键支撑位，跌破考虑减仓"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={onCancel}>取消</Button>
          <Button onClick={submit} disabled={!canSubmit}>创建</Button>
        </div>
      </CardContent>
    </Card>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-muted-foreground mb-1">{label}</label>
      {children}
    </div>
  );
}
