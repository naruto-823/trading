interface ApiResponse<T> {
  data: T | null;
  error: { code: string; message: string; retryable: boolean } | null;
}

async function request<T>(
  path: string,
  options?: RequestInit
): Promise<ApiResponse<T>> {
  const response = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    return {
      data: null,
      error: {
        code: "HTTP_ERROR",
        message: `HTTP ${response.status}: ${response.statusText}`,
        retryable: response.status >= 500,
      },
    };
  }
  return response.json();
}

export interface CashInfoBreakdown {
  currency: string;
  available: number;
  withdraw: number;
  frozen: number;
  settling: number;
}

export async function getAccount() {
  return request<{
    id: number;
    synced_at: string;
    currency: string;
    total_cash: number;
    net_assets: number;
    market_value: number;
    total_pnl: number;
    day_pnl: number;
    realized_day_pnl: number;
    // 今日已卖出标的对当日盈亏的贡献，按市场拆分（原币）。
    // Position.day_pnl 只覆盖当前仍持仓的，前端需要把这里的市场金额叠加到对应卡片。
    realized_day_pnl_by_market: Record<string, number>;
    // 融资 / 保证金（全部 HKD 口径）
    max_finance_amount: number;
    remaining_finance_amount: number;
    // 实际融资欠款（HKD），与长桥 app "融资欠款"字段一致（负数表示借款）
    outstanding_debt: number;
    init_margin: number;
    maintenance_margin: number;
    buy_power: number;
    margin_call: number;
    risk_level: number;
    cash_infos: CashInfoBreakdown[];
    // 同步时刻的汇率快照，键形如 "HKD_CNY" / "USD_HKD" / "USD_CNY" / "CNY_HKD"
    fx_rates: Record<string, number>;
  }>("/account");
}

export async function getPositions(market?: string) {
  const params = market ? `?market=${market}` : "";
  return request<
    Array<{
      id: number;
      symbol: string;
      market: string;
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
      synced_at: string;
    }>
  >(`/positions${params}`);
}

export async function getExecutions(params?: {
  symbol?: string;
  from?: string;
  to?: string;
  page?: number;
  size?: number;
}) {
  const searchParams = new URLSearchParams();
  if (params?.symbol) searchParams.set("symbol", params.symbol);
  if (params?.from) searchParams.set("from", params.from);
  if (params?.to) searchParams.set("to", params.to);
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.size) searchParams.set("size", String(params.size));
  const query = searchParams.toString();
  return request<{
    items: Array<{
      execution_id: string;
      order_id: string;
      symbol: string;
      market: string;
      side: string;
      price: number;
      quantity: number;
      trade_done_at: string;
      currency: string;
      commission: number;
      platform_fee: number;
    }>;
    total: number;
    page: number;
    size: number;
  }>(`/executions${query ? `?${query}` : ""}`);
}

export async function getOrders(params?: {
  symbol?: string;
  status?: string;
  from?: string;
  to?: string;
  page?: number;
  size?: number;
}) {
  const searchParams = new URLSearchParams();
  if (params?.symbol) searchParams.set("symbol", params.symbol);
  if (params?.status) searchParams.set("status", params.status);
  if (params?.from) searchParams.set("from", params.from);
  if (params?.to) searchParams.set("to", params.to);
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.size) searchParams.set("size", String(params.size));
  const query = searchParams.toString();
  return request<{
    items: Array<{
      order_id: string;
      symbol: string;
      market: string;
      side: string;
      order_type: string;
      status: string;
      submitted_qty: number;
      filled_qty: number;
      avg_price: number;
      submitted_at: string | null;
      updated_at: string | null;
    }>;
    total: number;
    page: number;
    size: number;
  }>(`/orders${query ? `?${query}` : ""}`);
}

export interface QuoteData {
  symbol: string;
  name: string;
  current_price: number;
  prev_close: number;
  open: number;
  high: number;
  low: number;
  last_done: number;
  volume: number;
  turnover: number;
  pre_market_price: number;
  pre_market_change: number;
  pre_market_change_ratio: number;
  post_market_price: number;
  post_market_change: number;
  post_market_change_ratio: number;
  trading_session: "pre" | "regular" | "post" | "overnight" | "closed";
  change: number;
  change_ratio: number;
  timestamp: string;
}

export async function getQuotes(symbols: string[]) {
  const symbolParam = symbols.join(",");
  return request<QuoteData[]>(`/quote?symbols=${encodeURIComponent(symbolParam)}`);
}

export async function getPnlSummary(groupBy = "symbol") {
  return request<
    Array<{
      group: string;
      total_pnl: number;
      realized_pnl: number;
      unrealized_pnl: number;
      market_value: number;
      cost_value: number;
    }>
  >(`/pnl/summary?group_by=${groupBy}`);
}

export async function syncAll() {
  return request<
    Array<{
      kind: string;
      status: string;
      rows_written: number;
      error: string | null;
    }>
  >("/sync/all", { method: "POST" });
}

export interface BriefingHeadline {
  title: string;
  url: string;
}

export interface BriefingStock {
  symbol: string;
  headlines: BriefingHeadline[];
  bullish: string;
  bearish: string;
  suggestion: string;
}

export interface BriefingContextItem {
  name: string;
  price: number | null;
  change_percent: number | null;
}

export interface BriefingData {
  generated_at: string;
  cache_hit: boolean;
  market_summary: string;
  stocks: BriefingStock[];
  overall_action: string;
  context: Record<string, BriefingContextItem>;
}

export async function getBriefing(forceRefresh = false) {
  const qs = forceRefresh ? "?force_refresh=true" : "";
  return request<BriefingData>(`/dashboard/briefing${qs}`);
}

export type SuggestionAction = "stop_loss" | "sell" | "buy" | "add";
export type SuggestionUrgency = "high" | "medium" | "low";

export interface SuggestionAffordability {
  status: "ok" | "tight" | "over";
  cost_hkd: number;
  buy_power_hkd: number;
  ratio_pct: number;
}

export interface Suggestion {
  id: string;                         // suggestion_key (symbol-action)
  row_id: string;                     // DB 主键，用于 dismiss API
  action: SuggestionAction;
  symbol: string;
  qty: string;
  price: string;
  urgency: SuggestionUrgency;
  thesis: string;
  data_points: string[];
  affordability?: SuggestionAffordability;
  dismissed: boolean;
  adopted_decision_id: string | null;
}

export interface SuggestionsData {
  generated_at: string;
  cache_hit: boolean;
  batch_id: string;
  summary: string;
  suggestions: Suggestion[];
}

export interface SuggestionBatch {
  batch_id: string;
  generated_at: string;
  summary: string;
  suggestions: Suggestion[];
}

export async function getSuggestions(forceRefresh = false) {
  const qs = forceRefresh ? "?force_refresh=true" : "";
  return request<SuggestionsData>(`/decisions/suggestions${qs}`);
}

export async function getSuggestionHistory(days = 7) {
  return request<SuggestionBatch[]>(`/decisions/suggestions/history?days=${days}`);
}

export async function dismissSuggestion(rowId: string) {
  return request<{ row_id: string; dismissed: boolean }>(
    `/decisions/suggestions/${rowId}/dismiss`,
    { method: "POST" },
  );
}

// ===== 决策日志（后端持久化）=====

export type DecisionAction = "buy" | "sell" | "add" | "stop_loss";
export type DecisionStatus = "pending" | "executed" | "abandoned";

export interface DecisionChecklist {
  currentLossPct: string;
  isLeveraged: boolean;
  thesisChanged: string;
  willExceedConcentration: boolean;
  catalyst: string;
  exitPlan: string;
}

export interface DecisionApi {
  id: string;
  created_at_ms: number;
  status: DecisionStatus;
  executed_at_ms: number | null;
  action: DecisionAction;
  symbol: string;
  qty: string;
  price: string;
  thesis: string;
  cooldown_hours: number;
  urgent_reason: string | null;
  checklist: DecisionChecklist | null;
  source: string;
  source_suggestion_id: string | null;
}

export interface DecisionCreatePayload {
  id?: string;
  action: DecisionAction;
  symbol: string;
  qty?: string;
  price?: string;
  thesis?: string;
  cooldown_hours: number;
  urgent_reason?: string | null;
  checklist?: DecisionChecklist | null;
  source?: string;
  source_suggestion_id?: string | null;
  created_at_ms?: number;
}

export async function listDecisions() {
  return request<DecisionApi[]>("/decisions");
}

export async function createDecision(payload: DecisionCreatePayload) {
  return request<DecisionApi>("/decisions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateDecisionStatus(id: string, status: DecisionStatus) {
  return request<DecisionApi>(`/decisions/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

export async function deleteDecision(id: string) {
  return request<{ id: string; deleted: boolean }>(`/decisions/${id}`, {
    method: "DELETE",
  });
}

// ===== 后台 jobs（APScheduler）=====

export interface JobStatus {
  id: string;
  name: string;
  next_run_at: string | null;
  trigger: string;
  last_run_at: string | null;
  last_status: "never" | "success" | "error";
  last_error: string | null;
  last_duration_ms: number | null;
  run_count: number;
}

export async function listJobs() {
  return request<JobStatus[]>("/system/jobs");
}

export async function runJob(jobId: string) {
  return request<{ job_id: string; triggered: boolean }>(
    `/system/jobs/${jobId}/run`,
    { method: "POST" },
  );
}

// ===== 告警规则 + Telegram =====

export type AlertCondition =
  | "price_above"
  | "price_below"
  | "day_change_pct_above"
  | "day_change_pct_below";

export interface AlertApi {
  id: string;
  created_at_ms: number;
  enabled: boolean;
  symbol: string;
  condition: AlertCondition;
  threshold: number;
  note: string;
  cooldown_minutes: number;
  last_triggered_at_ms: number | null;
  trigger_count: number;
}

export interface AlertCreatePayload {
  symbol: string;
  condition: AlertCondition;
  threshold: number;
  note?: string;
  cooldown_minutes?: number;
  enabled?: boolean;
}

export interface AlertUpdatePayload {
  enabled?: boolean;
  threshold?: number;
  note?: string;
  cooldown_minutes?: number;
  condition?: AlertCondition;
  reset_cooldown?: boolean;
}

export async function listAlerts() {
  return request<AlertApi[]>("/alerts");
}

export async function createAlert(payload: AlertCreatePayload) {
  return request<AlertApi>("/alerts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateAlert(id: string, payload: AlertUpdatePayload) {
  return request<AlertApi>(`/alerts/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteAlert(id: string) {
  return request<{ id: string; deleted: boolean }>(`/alerts/${id}`, {
    method: "DELETE",
  });
}

export async function getNotifyStatus() {
  return request<{ configured: boolean }>("/alerts/notify/status");
}

export async function testNotify() {
  return request<{ ok: boolean; detail: unknown }>(
    "/alerts/notify/test",
    { method: "POST" },
  );
}

export async function getSyncLogs(limit = 20) {
  return request<
    Array<{
      id: number;
      kind: string;
      started_at: string;
      finished_at: string | null;
      status: string;
      error: string | null;
      rows_written: number;
    }>
  >(`/sync/logs?limit=${limit}`);
}
