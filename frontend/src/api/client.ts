/**
 * Typed fetch wrapper for the Vigil REST API (see vigil/docs/API_SPEC.md).
 * Base path is '/api' (Vite dev proxy / nginx proxy to the backend).
 * Auth: `Authorization: Bearer <token>` with the token kept in
 * localStorage under 'vigil_token'. JSON error bodies ({detail}) surface
 * as ApiError instances.
 */

import type {
  AlertDetailResponse,
  AlertSummary,
  AppConfig,
  AuditRow,
  BacktestAccepted,
  BacktestDetailResponse,
  BacktestSummaryRow,
  CalendarItem,
  CompanyDetail,
  DataHealthResponse,
  EnginesResponse,
  FinancialsResponse,
  HealthResponse,
  Instrument,
  ModelVersionRow,
  NewBacktestRequest,
  NewPosition,
  NotificationRow,
  OpportunitiesResponse,
  OpportunityFilters,
  Paginated,
  PeersResponse,
  PortfolioResponse,
  PricesResponse,
  ScanAccepted,
  ScoreRunRow,
  SignalView,
  WatchlistItem,
} from './types';

const BASE = '/api';
const TOKEN_KEY = 'vigil_token';

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? '';
  } catch {
    return '';
  }
}

export function setToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // localStorage unavailable — token lives only for this page load.
  }
}

type QueryValue = string | number | boolean | undefined | null;

/** Build a query string, skipping undefined / null / empty-string values. */
export function qs(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    search.set(key, String(value));
  }
  const s = search.toString();
  return s ? `?${s}` : '';
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set('Accept', 'application/json');
  const token = getToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (init?.body !== undefined) headers.set('Content-Type', 'application/json');

  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, 'Network error — is the Vigil API running?');
  }

  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`;
    try {
      const body: unknown = await res.json();
      if (
        body !== null &&
        typeof body === 'object' &&
        'detail' in body &&
        typeof (body as { detail: unknown }).detail === 'string'
      ) {
        detail = (body as { detail: string }).detail;
      }
    } catch {
      // Non-JSON error body — keep the status text.
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

function get<T>(path: string): Promise<T> {
  return request<T>(path);
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: body === undefined ? JSON.stringify({}) : JSON.stringify(body),
  });
}

function del<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// Endpoint helpers — one function per API_SPEC route.
// ---------------------------------------------------------------------------

export const api = {
  // Health & ops
  health: () => get<HealthResponse>('/health'),
  dataHealth: () => get<DataHealthResponse>('/health/data'),
  config: () => get<AppConfig>('/config'),
  scan: (asOf?: string) => post<ScanAccepted>('/scan', asOf ? { as_of: asOf } : {}),
  modelVersions: () => get<ModelVersionRow[]>('/model-versions'),
  audit: (limit = 200) => get<{ items: AuditRow[] }>(`/audit${qs({ limit })}`),

  // Universe & companies
  instruments: (params: {
    market?: string;
    sector?: string;
    q?: string;
    active?: boolean;
    limit?: number;
    offset?: number;
  }) => get<Paginated<Instrument>>(`/instruments${qs(params)}`),
  company: (id: number | string) => get<CompanyDetail>(`/companies/${id}`),
  companyPrices: (id: number | string, days = 730) =>
    get<PricesResponse>(`/companies/${id}/prices${qs({ days })}`),
  companyFinancials: (id: number | string) =>
    get<FinancialsResponse>(`/companies/${id}/financials`),
  companyEngines: (id: number | string, runId?: number) =>
    get<EnginesResponse>(`/companies/${id}/engines${qs({ run_id: runId })}`),
  companyPeers: (id: number | string) => get<PeersResponse>(`/companies/${id}/peers`),
  companyAlerts: (id: number | string, limit = 50) =>
    get<{ items: AlertSummary[] }>(`/companies/${id}/alerts${qs({ limit })}`),
  companySignals: (id: number | string) => get<{ items: SignalView[] }>(`/companies/${id}/signals`),

  // Scores & opportunities
  runs: (limit = 20) => get<{ items: ScoreRunRow[] }>(`/runs${qs({ limit })}`),
  run: (id: number | string) => get<ScoreRunRow>(`/runs/${id}`),
  opportunities: (filters: OpportunityFilters) =>
    get<OpportunitiesResponse>(`/opportunities${qs({ ...filters })}`),

  // Alerts
  alerts: (params: {
    family?: string;
    state?: string;
    priority?: string;
    horizon?: string;
    unread_only?: boolean;
    instrument_id?: number;
    since?: string;
    limit?: number;
    offset?: number;
  }) => get<Paginated<AlertSummary>>(`/alerts${qs(params)}`),
  alert: (id: number | string) => get<AlertDetailResponse>(`/alerts/${id}`),
  markAlertRead: (id: number | string) => post<unknown>(`/alerts/${id}/read`),
  markAlertUnread: (id: number | string) => post<unknown>(`/alerts/${id}/unread`),

  // Signals
  signals: (params: {
    state?: string;
    family?: string;
    active?: boolean;
    instrument_id?: number;
    limit?: number;
    offset?: number;
  }) => get<Paginated<SignalView>>(`/signals${qs(params)}`),
  signal: (id: number | string) => get<SignalView>(`/signals/${id}`),

  // Portfolio & watchlist
  portfolio: () => get<PortfolioResponse>('/portfolio'),
  addPosition: (body: NewPosition) => post<{ id: number }>('/portfolio', body),
  closePosition: (positionId: number) => del<unknown>(`/portfolio/${positionId}`),
  watchlist: () => get<{ items: WatchlistItem[] }>('/watchlist'),
  addToWatchlist: (instrumentId: number, notes?: string) =>
    post<{ id: number }>('/watchlist', { instrument_id: instrumentId, notes }),
  removeFromWatchlist: (watchlistItemId: number) => del<unknown>(`/watchlist/${watchlistItemId}`),

  // Calendar
  calendar: (days = 60, binaryOnly = false) =>
    get<{ items: CalendarItem[] }>(`/calendar${qs({ days, binary_only: binaryOnly || undefined })}`),

  // Backtests
  backtests: () => get<{ items: BacktestSummaryRow[] }>('/backtests'),
  backtest: (id: number | string) => get<BacktestDetailResponse>(`/backtests/${id}`),
  newBacktest: (body: NewBacktestRequest) => post<BacktestAccepted>('/backtests', body),

  // Notifications
  notifications: (limit = 100) => get<{ items: NotificationRow[] }>(`/notifications${qs({ limit })}`),
};
