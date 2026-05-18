import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, CheckCircle2, XCircle, AlertTriangle, Clock, Sparkles, RefreshCw, ChevronDown, ChevronUp } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  createDecision,
  deleteDecision,
  dismissSuggestion,
  getSuggestionHistory,
  getSuggestions,
  listDecisions,
  updateDecisionStatus,
  type DecisionApi,
  type DecisionCreatePayload,
  type Suggestion,
  type SuggestionBatch,
} from "@/api/client";

type Action = "buy" | "sell" | "add" | "stop_loss";
type Status = "pending" | "executed" | "abandoned";

interface Checklist {
  currentLossPct: string;       // 当前浮亏%
  isLeveraged: boolean;          // 杠杆/期权
  thesisChanged: string;         // 基本面新数据
  willExceedConcentration: boolean; // 补仓后是否超 8%
  catalyst: string;              // 具体催化剂
  exitPlan: string;              // 退出计划
}

interface Decision {
  id: string;
  createdAt: number;
  status: Status;
  executedAt?: number;
  action: Action;
  symbol: string;
  qty: string;
  price: string;
  thesis: string;
  checklist?: Checklist;
  // 默认冷静期按动作类型给（add=24h, buy=4h, sell=1h, stop_loss=0），可被用户覆盖
  cooldownHours: number;
  // 如果用户把冷静期改为 0（紧急执行），强制写时效原因
  urgentReason?: string;
  // 来源追踪（用于 AI 建议采纳率分析）
  source?: string;
  sourceSuggestionId?: string;
}

const STORAGE_KEY = "decisions.v1";

// 默认冷静期（小时）：按动作类型决定
const DEFAULT_COOLDOWN: Record<Action, number> = {
  add: 24,        // 补仓最容易出问题，强制 24h
  buy: 4,         // 新仓给点缓冲
  sell: 1,        // 止盈基本即时
  stop_loss: 0,   // 止损必须立刻执行
};

const ACTION_LABEL: Record<Action, string> = {
  buy: "买入",
  sell: "卖出",
  add: "补仓",
  stop_loss: "止损",
};

const ACTION_COLOR: Record<Action, string> = {
  buy: "bg-blue-100 text-blue-700 dark:bg-blue-950/40 dark:text-blue-400",
  sell: "bg-gray-100 text-gray-700 dark:bg-gray-800/40 dark:text-gray-400",
  add: "bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-500",
  stop_loss: "bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-500",
};

// ====== API ↔ 内部 shape 互转 ======
// 内部一直用 camelCase（form / row 都写好了），后端用 snake_case + ms 时间戳。

function apiToInternal(d: DecisionApi): Decision {
  return {
    id: d.id,
    createdAt: d.created_at_ms,
    status: d.status,
    executedAt: d.executed_at_ms ?? undefined,
    action: d.action,
    symbol: d.symbol,
    qty: d.qty,
    price: d.price,
    thesis: d.thesis,
    checklist: d.checklist ?? undefined,
    cooldownHours: d.cooldown_hours,
    urgentReason: d.urgent_reason ?? undefined,
    source: d.source,
    sourceSuggestionId: d.source_suggestion_id ?? undefined,
  };
}

function internalToCreatePayload(d: Decision): DecisionCreatePayload {
  return {
    id: d.id,
    action: d.action,
    symbol: d.symbol,
    qty: d.qty,
    price: d.price,
    thesis: d.thesis,
    cooldown_hours: d.cooldownHours,
    urgent_reason: d.urgentReason ?? null,
    checklist: d.checklist ?? null,
    source: d.source ?? "manual",
    source_suggestion_id: d.sourceSuggestionId ?? null,
    created_at_ms: d.createdAt,
  };
}

// ====== localStorage 旧数据迁移（一次性） ======
function readLegacyDecisions(): Decision[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function clearLegacyDecisions() {
  localStorage.removeItem(STORAGE_KEY);
}

function relativeTime(ts: number): string {
  const diff = Date.now() - ts;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} 小时前`;
  const days = Math.floor(hrs / 24);
  return `${days} 天前`;
}

export default function Decisions() {
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  // 从 AI 建议"采纳"过来的预填内容，传给 NewDecisionForm 当 initial
  const [formInitial, setFormInitial] = useState<Partial<Decision> | null>(null);

  const query = useQuery({
    queryKey: ["decisions"],
    queryFn: () => listDecisions(),
  });
  const items: Decision[] = useMemo(() => {
    const list = query.data?.data ?? [];
    return list.map(apiToInternal);
  }, [query.data]);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["decisions"] });

  const createMutation = useMutation({
    mutationFn: (d: Decision) => createDecision(internalToCreatePayload(d)),
    onSuccess: invalidate,
  });
  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: "executed" | "abandoned" }) =>
      updateDecisionStatus(id, status),
    onSuccess: invalidate,
  });
  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteDecision(id),
    onSuccess: invalidate,
  });

  // 一次性迁移：localStorage 的旧数据搬到后端（只在 query 成功且后端空 + localStorage 有数据时做）
  const migratedRef = useRef(false);
  useEffect(() => {
    if (migratedRef.current) return;
    if (query.isLoading || query.isError) return;
    const legacy = readLegacyDecisions();
    if (legacy.length === 0) return;
    migratedRef.current = true;
    // 串行 POST 避免并发把后端搞混
    (async () => {
      for (const d of legacy) {
        try {
          await createDecision(internalToCreatePayload(d));
        } catch (e) {
          console.warn("migrate decision failed", d.id, e);
        }
      }
      clearLegacyDecisions();
      invalidate();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.isLoading, query.isError]);

  const grouped = useMemo(() => {
    const now = Date.now();
    const fresh: Decision[] = [];
    const stale: Decision[] = [];
    const executed: Decision[] = [];
    const abandoned: Decision[] = [];
    for (const d of items) {
      if (d.status === "executed") executed.push(d);
      else if (d.status === "abandoned") abandoned.push(d);
      else {
        // 用决策自己的冷静期长度（旧记录没有这字段时按 24h 兜底）
        const hours = d.cooldownHours ?? 24;
        const coolingCutoff = now - hours * 3600 * 1000;
        if (d.createdAt > coolingCutoff) fresh.push(d);
        else stale.push(d);
      }
    }
    const sortDesc = (a: Decision, b: Decision) => b.createdAt - a.createdAt;
    return {
      fresh: fresh.sort(sortDesc),
      stale: stale.sort(sortDesc),
      executed: executed.sort(sortDesc),
      abandoned: abandoned.sort(sortDesc),
    };
  }, [items]);

  // 这两个状态变更只支持已执行 / 作废（pending 不暴露）—— 跟原前端语义一致
  const markStatus = (id: string, status: "executed" | "abandoned") => {
    statusMutation.mutate({ id, status });
  };

  const remove = (id: string) => {
    if (!confirm("删除这条决策记录？")) return;
    deleteMutation.mutate(id);
  };

  const add = (d: Decision) => {
    createMutation.mutate(d);
    setShowForm(false);
    setFormInitial(null);
  };

  const adoptSuggestion = (s: Suggestion) => {
    // 把 AI 建议预填进新建表单（用户仍可改、仍走冷静期）
    setFormInitial({
      action: s.action,
      symbol: s.symbol,
      qty: s.qty,
      price: s.price,
      thesis: `${s.thesis}\n\n【AI 引用的数据点】\n${s.data_points.map((d) => `· ${d}`).join("\n")}`,
      source: "ai_suggestion",
      sourceSuggestionId: s.id,
    });
    setShowForm(true);
    // 滚到表单
    setTimeout(() => window.scrollTo({ top: 200, behavior: "smooth" }), 50);
  };

  return (
    <div className="p-6 space-y-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">交易决策日志</h2>
          <p className="text-sm text-muted-foreground mt-1">
            决策前写下来，过冷静期再看 —— 损失厌恶的最便宜护栏
          </p>
        </div>
        <Button onClick={() => { setFormInitial(null); setShowForm((v) => !v); }}>
          <Plus className="h-4 w-4 mr-1" />
          新建决策
        </Button>
      </div>

      {showForm && (
        <NewDecisionForm
          onSubmit={add}
          onCancel={() => { setShowForm(false); setFormInitial(null); }}
          initial={formInitial}
        />
      )}

      <SuggestionsSection onAdopt={adoptSuggestion} />
      <SuggestionHistorySection />


      {/* 冷静期里（按决策自己的 cooldownHours） */}
      <Section
        icon={<Clock className="h-4 w-4 text-blue-600" />}
        title="冷静期中"
        empty="还没有新决策。下决心前先记一笔。"
        items={grouped.fresh}
        render={(d) => {
          const hours = d.cooldownHours ?? 24;
          const remaining = d.createdAt + hours * 3600 * 1000 - Date.now();
          const remainText =
            remaining > 3600 * 1000
              ? `${Math.ceil(remaining / 3600000)} 小时后可执行`
              : `${Math.max(1, Math.ceil(remaining / 60000))} 分钟后可执行`;
          return (
            <DecisionRow
              key={d.id}
              d={d}
              badge={`${relativeTime(d.createdAt)} · ${remainText}`}
              badgeCls="text-blue-600"
              onExecuted={() => markStatus(d.id, "executed")}
              onAbandon={() => markStatus(d.id, "abandoned")}
              onDelete={() => remove(d.id)}
            />
          );
        }}
      />

      {/* 冷静期结束，待拍板（含 0h 即时决策，直接进这里） */}
      <Section
        icon={<AlertTriangle className="h-4 w-4 text-amber-600" />}
        title="冷静期结束 —— 该拍板了"
        empty="没有待拍板的决策。"
        items={grouped.stale}
        render={(d) => (
          <DecisionRow
            key={d.id}
            d={d}
            badge={`${relativeTime(d.createdAt)}${d.urgentReason ? " · ⚡紧急" : ""}`}
            badgeCls="text-amber-600 font-medium"
            highlight
            onExecuted={() => markStatus(d.id, "executed")}
            onAbandon={() => markStatus(d.id, "abandoned")}
            onDelete={() => remove(d.id)}
          />
        )}
      />

      {/* 已执行 + 已作废，折叠展示 */}
      <details className="border rounded-lg p-4">
        <summary className="cursor-pointer text-sm font-medium">
          历史（已执行 {grouped.executed.length} 条 · 已作废 {grouped.abandoned.length} 条）
        </summary>
        <div className="mt-4 space-y-3">
          {[...grouped.executed, ...grouped.abandoned].map((d) => (
            <DecisionRow
              key={d.id}
              d={d}
              badge={d.status === "executed" ? `已执行 · ${relativeTime(d.executedAt ?? d.createdAt)}` : `已作废`}
              badgeCls={d.status === "executed" ? "text-green-600" : "text-muted-foreground"}
              compact
              onDelete={() => remove(d.id)}
            />
          ))}
        </div>
      </details>
    </div>
  );
}

function Section({
  icon,
  title,
  empty,
  items,
  render,
}: {
  icon: React.ReactNode;
  title: string;
  empty: string;
  items: Decision[];
  render: (d: Decision) => React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          {icon}
          {title}
          <span className="text-xs font-normal text-muted-foreground ml-1">
            ({items.length})
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">{empty}</p>
        ) : (
          <div className="space-y-3">{items.map(render)}</div>
        )}
      </CardContent>
    </Card>
  );
}

function DecisionRow({
  d,
  badge,
  badgeCls,
  highlight,
  compact,
  onExecuted,
  onAbandon,
  onDelete,
}: {
  d: Decision;
  badge: string;
  badgeCls: string;
  highlight?: boolean;
  compact?: boolean;
  onExecuted?: () => void;
  onAbandon?: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      className={`rounded-md border p-3 ${
        highlight ? "border-amber-500/40 bg-amber-50/40 dark:bg-amber-950/10" : ""
      }`}
    >
      <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${ACTION_COLOR[d.action]}`}>
            {ACTION_LABEL[d.action]}
          </span>
          <span className="font-mono font-semibold">{d.symbol}</span>
          {d.qty && <span className="text-sm text-muted-foreground">× {d.qty}</span>}
          {d.price && <span className="text-sm text-muted-foreground">@ {d.price}</span>}
        </div>
        <span className={`text-xs ${badgeCls}`}>{badge}</span>
      </div>

      {!compact && d.thesis && (
        <p className="text-sm whitespace-pre-wrap text-foreground/90 pb-2">{d.thesis}</p>
      )}

      {!compact && d.urgentReason && (
        <div className="text-xs text-amber-700 dark:text-amber-500 bg-amber-50/40 dark:bg-amber-950/10 rounded px-2 py-1 mb-2">
          ⚡ 跳过冷静期 · 时效原因：{d.urgentReason}
        </div>
      )}

      {!compact && d.checklist && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-amber-700 dark:text-amber-500 font-medium">
            ⚠ 补仓检查清单
          </summary>
          <dl className="text-xs space-y-1 mt-2 pl-3">
            <ChecklistItem label="当前浮亏" value={d.checklist.currentLossPct + "%"} />
            <ChecklistItem label="是否杠杆/期权" value={d.checklist.isLeveraged ? "是 ⚠" : "否"} />
            <ChecklistItem label="基本面有新数据吗" value={d.checklist.thesisChanged || "—"} />
            <ChecklistItem
              label="补仓后是否超 8% 集中度"
              value={d.checklist.willExceedConcentration ? "会 ⚠" : "不会"}
            />
            <ChecklistItem label="具体催化剂" value={d.checklist.catalyst || "—"} />
            <ChecklistItem label="退出计划" value={d.checklist.exitPlan || "—"} />
          </dl>
        </details>
      )}

      <div className="flex items-center justify-end gap-2 mt-2">
        {onExecuted && (
          <Button variant="outline" size="sm" onClick={onExecuted} className="h-7 text-xs">
            <CheckCircle2 className="h-3 w-3 mr-1" />
            已执行
          </Button>
        )}
        {onAbandon && (
          <Button variant="outline" size="sm" onClick={onAbandon} className="h-7 text-xs">
            <XCircle className="h-3 w-3 mr-1" />
            作废
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={onDelete} className="h-7 text-xs">
          <Trash2 className="h-3 w-3" />
        </Button>
      </div>
    </div>
  );
}

function ChecklistItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex">
      <dt className="text-muted-foreground w-36 flex-shrink-0">{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function NewDecisionForm({
  onSubmit,
  onCancel,
  initial,
}: {
  onSubmit: (d: Decision) => void;
  onCancel: () => void;
  initial?: Partial<Decision> | null;
}) {
  const [action, setAction] = useState<Action>(initial?.action ?? "buy");
  const [symbol, setSymbol] = useState(initial?.symbol ?? "");
  const [qty, setQty] = useState(initial?.qty ?? "");
  const [price, setPrice] = useState(initial?.price ?? "");
  const [thesis, setThesis] = useState(initial?.thesis ?? "");
  const [urgent, setUrgent] = useState(false);
  const [urgentReason, setUrgentReason] = useState("");

  // 补仓检查清单
  const [currentLossPct, setCurrentLossPct] = useState("");
  const [isLeveraged, setIsLeveraged] = useState(false);
  const [thesisChanged, setThesisChanged] = useState("");
  const [willExceed, setWillExceed] = useState(false);
  const [catalyst, setCatalyst] = useState("");
  const [exitPlan, setExitPlan] = useState("");

  const defaultCooldown = DEFAULT_COOLDOWN[action];
  const effectiveCooldown = urgent ? 0 : defaultCooldown;
  // 补仓动作不允许通过 urgent 跳过 24h（这是这套系统的核心目的）
  const canBeUrgent = action !== "add";

  const requireChecklist = action === "add";
  const checklistComplete =
    !requireChecklist ||
    (currentLossPct.trim() && thesisChanged.trim() && catalyst.trim() && exitPlan.trim());

  // 勾了"紧急执行"必须写时效原因
  const urgentReasonValid = !urgent || urgentReason.trim().length > 0;

  const canSubmit = symbol.trim() && thesis.trim() && checklistComplete && urgentReasonValid;

  const submit = () => {
    if (!canSubmit) return;
    const decision: Decision = {
      id: crypto.randomUUID(),
      createdAt: Date.now(),
      status: "pending",
      action,
      symbol: symbol.trim().toUpperCase(),
      qty: qty.trim(),
      price: price.trim(),
      thesis: thesis.trim(),
      cooldownHours: effectiveCooldown,
      urgentReason: urgent ? urgentReason.trim() : undefined,
      checklist: requireChecklist
        ? {
            currentLossPct: currentLossPct.trim(),
            isLeveraged,
            thesisChanged: thesisChanged.trim(),
            willExceedConcentration: willExceed,
            catalyst: catalyst.trim(),
            exitPlan: exitPlan.trim(),
          }
        : undefined,
      source: initial?.source,
      sourceSuggestionId: initial?.sourceSuggestionId,
    };
    onSubmit(decision);
  };

  return (
    <Card className="border-blue-500/30">
      <CardHeader>
        <CardTitle className="text-base">新建决策</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <Field label={`动作（默认冷静期 ${defaultCooldown}h）`}>
            <select
              className="w-full rounded-md border px-2 py-1.5 text-sm bg-background"
              value={action}
              onChange={(e) => {
                setAction(e.target.value as Action);
                setUrgent(false);
                setUrgentReason("");
              }}
            >
              <option value="buy">买入（新仓）· 默认 4h</option>
              <option value="add">补仓（亏损中加仓）· 强制 24h</option>
              <option value="sell">卖出（止盈/减仓）· 默认 1h</option>
              <option value="stop_loss">止损 · 默认 0h（立即）</option>
            </select>
          </Field>
          <Field label="标的">
            <input
              className="w-full rounded-md border px-2 py-1.5 text-sm bg-background font-mono"
              placeholder="MSFT.US / 700.HK"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
            />
          </Field>
          <Field label="数量（可选）">
            <input
              className="w-full rounded-md border px-2 py-1.5 text-sm bg-background"
              placeholder="如 10 股"
              value={qty}
              onChange={(e) => setQty(e.target.value)}
            />
          </Field>
          <Field label="目标价（可选）">
            <input
              className="w-full rounded-md border px-2 py-1.5 text-sm bg-background"
              placeholder="如 419.50"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
            />
          </Field>
        </div>

        <Field label="决策理由（必填）">
          <textarea
            className="w-full rounded-md border px-2 py-1.5 text-sm bg-background min-h-[80px]"
            placeholder="为什么要做这个动作？基于什么信息？目标是什么？"
            value={thesis}
            onChange={(e) => setThesis(e.target.value)}
          />
        </Field>

        {/* 紧急执行覆盖：补仓动作禁用此选项（核心护栏不许跳过） */}
        {canBeUrgent && (
          <div className="rounded-md border p-3 space-y-2">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={urgent}
                onChange={(e) => setUrgent(e.target.checked)}
              />
              <span>
                紧急执行（跳过 {defaultCooldown}h 冷静期）
                {urgent && (
                  <span className="ml-2 text-xs text-amber-600">
                    需填时效原因，会记入历史用于复盘
                  </span>
                )}
              </span>
            </label>
            {urgent && (
              <textarea
                className="w-full rounded-md border px-2 py-1.5 text-sm bg-background min-h-[60px]"
                placeholder="为什么必须现在？（如：盘前突发 / 突破关键位 / 财报反应 / earnings 后窗口期）"
                value={urgentReason}
                onChange={(e) => setUrgentReason(e.target.value)}
              />
            )}
          </div>
        )}

        {!canBeUrgent && (
          <div className="rounded-md border border-amber-500/40 bg-amber-50/40 dark:bg-amber-950/10 p-3 text-xs text-amber-700 dark:text-amber-500">
            🛡️ 补仓动作不能跳过 24h 冷静期 —— 这正是这套系统对你最有价值的保护
          </div>
        )}

        {requireChecklist && (
          <div className="rounded-md border border-amber-500/40 bg-amber-50/40 dark:bg-amber-950/10 p-3 space-y-3">
            <div className="text-sm font-medium text-amber-700 dark:text-amber-500">
              ⚠ 补仓检查清单（5 问，必填）
            </div>
            <Field label="① 当前浮亏百分比">
              <input
                className="w-full rounded-md border px-2 py-1.5 text-sm bg-background"
                placeholder="如 -27"
                value={currentLossPct}
                onChange={(e) => setCurrentLossPct(e.target.value)}
              />
            </Field>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={isLeveraged}
                onChange={(e) => setIsLeveraged(e.target.checked)}
              />
              <span>
                ② 是否杠杆 ETF / 期权？
                {isLeveraged && (
                  <span className="ml-2 text-red-600 text-xs font-medium">
                    🚨 杠杆产品禁止补仓
                  </span>
                )}
              </span>
            </label>
            <Field label="③ 基本面有新利好数据吗（具体说明）">
              <textarea
                className="w-full rounded-md border px-2 py-1.5 text-sm bg-background min-h-[60px]"
                placeholder={"如：Q1 财报超预期 / 新产品发布 / 监管利好…（没有就老实写「没有」）"}
                value={thesisChanged}
                onChange={(e) => setThesisChanged(e.target.value)}
              />
            </Field>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={willExceed}
                onChange={(e) => setWillExceed(e.target.checked)}
              />
              <span>
                ④ 补仓后该标的占组合会超过 8% 吗？
                {willExceed && (
                  <span className="ml-2 text-amber-600 text-xs">
                    ⚠ 集中度过高
                  </span>
                )}
              </span>
            </label>
            <Field label="⑤ 具体催化剂 + 退出计划">
              <input
                className="w-full rounded-md border px-2 py-1.5 text-sm bg-background mb-2"
                placeholder="催化剂（如：5/29 发布会 / 6 月降息）"
                value={catalyst}
                onChange={(e) => setCatalyst(e.target.value)}
              />
              <input
                className="w-full rounded-md border px-2 py-1.5 text-sm bg-background"
                placeholder="退出计划（如：涨到 X 价位减仓 / 跌破 Y 止损）"
                value={exitPlan}
                onChange={(e) => setExitPlan(e.target.value)}
              />
            </Field>
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={onCancel}>
            取消
          </Button>
          <Button onClick={submit} disabled={!canSubmit}>
            {effectiveCooldown === 0 ? "立即记入（无冷静期）" : `记入冷静期（${effectiveCooldown}h）`}
          </Button>
        </div>
        {!urgentReasonValid && (
          <p className="text-xs text-amber-600 text-right">勾了"紧急执行"必须写时效原因</p>
        )}
        {!canSubmit && requireChecklist && !checklistComplete && (
          <p className="text-xs text-muted-foreground text-right">
            补仓决策必须填完所有问题才能提交
          </p>
        )}
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

// ---- AI 建议 ----

const ACTION_LABEL_EN: Record<Action, string> = ACTION_LABEL;
const URGENCY_ICON: Record<string, string> = { high: "🔴", medium: "🟡", low: "🟢" };

function SuggestionsSection({ onAdopt }: { onAdopt: (s: Suggestion) => void }) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const query = useQuery({
    queryKey: ["suggestions"],
    queryFn: () => getSuggestions(false),
    staleTime: 15 * 60 * 1000,
  });

  const refreshMutation = useMutation({
    mutationFn: () => getSuggestions(true),
    onSuccess: (data) => {
      queryClient.setQueryData(["suggestions"], data);
      queryClient.invalidateQueries({ queryKey: ["suggestionsHistory"] });
    },
  });

  // 后端持久化 dismiss：driven by 服务端 dismissed 字段，前端只发请求 + invalidate
  const dismissMutation = useMutation({
    mutationFn: (rowId: string) => dismissSuggestion(rowId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["suggestions"] });
      queryClient.invalidateQueries({ queryKey: ["suggestionsHistory"] });
    },
  });

  const data = query.data?.data;
  const isLoading = query.isLoading || refreshMutation.isPending;
  // 后端的 /current 端点已经过滤了 dismissed，前端就直接展示
  const visible = data?.suggestions ?? [];

  return (
    <Card className="border-blue-500/30">
      <CardHeader className="flex flex-row items-center justify-between pb-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-blue-600" />
          <CardTitle className="text-base">AI 决策建议</CardTitle>
          {data && (
            <span className="text-xs text-muted-foreground ml-1">
              {data.cache_hit ? "缓存" : "新鲜"} · {visible.length} 条 · {relativeTime(new Date(data.generated_at).getTime())}
            </span>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => refreshMutation.mutate()}
          disabled={isLoading}
        >
          <RefreshCw className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        {query.isLoading && !data && (
          <div className="space-y-2">
            <div className="h-4 w-3/4 bg-muted rounded animate-pulse" />
            <div className="h-4 w-2/3 bg-muted rounded animate-pulse" />
            <p className="text-xs text-muted-foreground pt-2">AI 分析中，约 20-40 秒…</p>
          </div>
        )}
        {query.isError && (
          <p className="text-sm text-red-600">建议加载失败：{String(query.error)}</p>
        )}

        {data && data.summary && (
          <div className="text-sm text-muted-foreground bg-blue-50/40 dark:bg-blue-950/10 rounded p-2">
            🧭 <span className="font-medium">整体策略：</span>{data.summary}
          </div>
        )}

        {visible.length === 0 && data && (
          <p className="text-sm text-muted-foreground">
            目前没有可展示的建议（{data.suggestions.length > 0 ? "全部已被你驳回" : "AI 暂无具体建议"}）
          </p>
        )}

        {visible.map((s) => {
          const isExpanded = expanded[s.id] ?? false;
          return (
            <div key={s.id} className="rounded-md border p-3 space-y-2">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-2">
                  <span className="text-base">{URGENCY_ICON[s.urgency] ?? ""}</span>
                  <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${ACTION_COLOR[s.action as Action]}`}>
                    {ACTION_LABEL_EN[s.action as Action] ?? s.action}
                  </span>
                  <span className="font-mono font-semibold">{s.symbol}</span>
                  {s.qty && <span className="text-sm text-muted-foreground">× {s.qty}</span>}
                  {s.price && <span className="text-sm text-muted-foreground">@ {s.price}</span>}
                </div>
                <span className="text-xs text-muted-foreground">紧急度 {s.urgency}</span>
              </div>

              {s.affordability && s.affordability.status !== "ok" && (
                <div
                  className={`text-xs rounded px-2 py-1 ${
                    s.affordability.status === "over"
                      ? "bg-red-50 text-red-700 dark:bg-red-950/30 dark:text-red-400"
                      : "bg-amber-50 text-amber-700 dark:bg-amber-950/30 dark:text-amber-500"
                  }`}
                >
                  {s.affordability.status === "over" ? "🛑" : "⚠️"} 估算成本 HK${s.affordability.cost_hkd.toLocaleString()} ·
                  占购买力 {s.affordability.ratio_pct}%
                  {s.affordability.status === "over" ? "（超出可用，需大幅减 qty）" : "（接近上限，建议减 qty）"}
                </div>
              )}

              <p className="text-sm whitespace-pre-wrap">{s.thesis}</p>

              {s.data_points.length > 0 && (
                <div>
                  <button
                    onClick={() => setExpanded((e) => ({ ...e, [s.id]: !e[s.id] }))}
                    className="text-xs text-blue-600 hover:underline flex items-center gap-1"
                  >
                    {isExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                    AI 引用的 {s.data_points.length} 条数据点
                  </button>
                  {isExpanded && (
                    <ul className="text-xs text-muted-foreground mt-1.5 space-y-0.5 pl-4">
                      {s.data_points.map((d, i) => (
                        <li key={i} className="list-disc">{d}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              <div className="flex items-center justify-end gap-2 pt-1">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => dismissMutation.mutate(s.row_id)}
                  disabled={dismissMutation.isPending}
                  className="h-7 text-xs"
                >
                  <XCircle className="h-3 w-3 mr-1" />
                  驳回
                </Button>
                <Button size="sm" onClick={() => onAdopt(s)} className="h-7 text-xs">
                  <CheckCircle2 className="h-3 w-3 mr-1" />
                  采纳到新建表单
                </Button>
              </div>
            </div>
          );
        })}

        {data && (
          <p className="text-[10px] text-muted-foreground pt-2 border-t">
            AI 建议仅供参考，可能存在事实错误（如未识别 covered call 等）。
            采纳后仍需走冷静期 + 个人审定。
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ---- AI 建议历史区 ----

function SuggestionHistorySection() {
  const [open, setOpen] = useState(false);
  const query = useQuery({
    queryKey: ["suggestionsHistory"],
    queryFn: () => getSuggestionHistory(14),
    enabled: open,
    staleTime: 5 * 60 * 1000,
  });
  const batches: SuggestionBatch[] = query.data?.data ?? [];

  return (
    <Card>
      <CardHeader
        className="cursor-pointer select-none pb-3"
        onClick={() => setOpen((v) => !v)}
      >
        <CardTitle className="text-base flex items-center justify-between">
          <span className="flex items-center gap-2">
            📜 历史 AI 建议
            {open && (
              <span className="text-xs font-normal text-muted-foreground ml-1">
                近 14 天 · {batches.length} 批
              </span>
            )}
          </span>
          {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </CardTitle>
      </CardHeader>
      {open && (
        <CardContent className="space-y-4">
          {query.isLoading && <p className="text-sm text-muted-foreground">加载中…</p>}
          {query.isError && (
            <p className="text-sm text-red-600">加载失败：{String(query.error)}</p>
          )}
          {!query.isLoading && batches.length === 0 && (
            <p className="text-sm text-muted-foreground">近 14 天暂无 AI 建议历史。</p>
          )}
          {batches.map((b) => {
            const total = b.suggestions.length;
            const adopted = b.suggestions.filter((s) => s.adopted_decision_id).length;
            const dismissed = b.suggestions.filter(
              (s) => s.dismissed && !s.adopted_decision_id,
            ).length;
            const pending = total - adopted - dismissed;
            return (
              <details key={b.batch_id} className="border rounded-md p-3">
                <summary className="cursor-pointer flex items-center justify-between text-sm">
                  <span className="text-muted-foreground">
                    {new Date(b.generated_at).toLocaleString("zh-CN")} · {total} 条
                  </span>
                  <span className="text-xs space-x-2">
                    <span className="text-green-600">采纳 {adopted}</span>
                    <span className="text-muted-foreground">驳回 {dismissed}</span>
                    <span className="text-amber-600">未处理 {pending}</span>
                  </span>
                </summary>
                {b.summary && (
                  <p className="text-xs text-muted-foreground mt-2 italic">{b.summary}</p>
                )}
                <div className="space-y-2 mt-2">
                  {b.suggestions.map((s) => {
                    const statusBadge = s.adopted_decision_id
                      ? { text: "✓ 采纳", cls: "text-green-700 dark:text-green-500" }
                      : s.dismissed
                        ? { text: "✗ 驳回", cls: "text-muted-foreground line-through" }
                        : { text: "○ 未处理", cls: "text-amber-600" };
                    return (
                      <div
                        key={s.row_id}
                        className="text-xs flex items-center gap-2 flex-wrap"
                      >
                        <span>{URGENCY_ICON[s.urgency] ?? ""}</span>
                        <span
                          className={`px-1.5 py-0.5 rounded font-medium ${ACTION_COLOR[s.action as Action]}`}
                        >
                          {ACTION_LABEL_EN[s.action as Action] ?? s.action}
                        </span>
                        <span className="font-mono">{s.symbol}</span>
                        {s.qty && <span className="text-muted-foreground">× {s.qty}</span>}
                        <span
                          className={`${statusBadge.cls} text-[11px] font-medium ml-auto`}
                        >
                          {statusBadge.text}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </details>
            );
          })}
        </CardContent>
      )}
    </Card>
  );
}
