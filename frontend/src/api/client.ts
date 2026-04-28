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
  trading_session: "pre" | "regular" | "post" | "closed";
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
