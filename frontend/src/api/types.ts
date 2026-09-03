/**
 * TypeScript mirrors of the REST responses documented in vigil/docs/API_SPEC.md
 * and of the alert payload schema in vigil/backend/src/vigil/schemas/alerts.py.
 * All timestamps are ISO-8601 UTC strings; dates are YYYY-MM-DD strings.
 */

// ---------------------------------------------------------------------------
// Common
// ---------------------------------------------------------------------------

export type Horizon = 'short' | 'medium' | 'long';
export type Direction = 'supports' | 'contradicts' | 'neutral';

export interface Paginated<T> {
  items: T[];
  total: number;
}

export interface SourceRef {
  provider: string;
  source_type: string;
  reference: string;
  published_at: string | null;
  retrieved_at?: string | null;
  freshness_days: number | null;
}

export interface Evidence {
  key: string;
  statement: string;
  value: number | string | null;
  direction: Direction;
  pillar: string;
  source: SourceRef;
  as_of: string;
}

export interface GateResult {
  passed: boolean;
  failures: string[];
  reward_risk: number | null;
}

export interface HorizonScore {
  horizon: Horizon;
  opportunity: number;
  confidence: number;
  risk: number;
  components: Record<string, number>;
  abstained: boolean;
  abstain_reasons: string[];
  gate: GateResult | null;
  explanation: string[];
}

export interface Scenario {
  name: 'base' | 'bull' | 'bear';
  price: number;
  probability_note: string;
  rationale: string;
}

export interface EntryPlan {
  zone_low: number | null;
  zone_high: number | null;
  stop: number | null;
  conditions_before_entry: string[];
  invalidation_conditions: string[];
  fundamental_invalidation: string[];
  target_low: number | null;
  target_high: number | null;
  scenarios: Scenario[];
  trim_conditions: string[];
  exit_conditions: string[];
  reward_risk: number | null;
}

// ---------------------------------------------------------------------------
// Health & ops
// ---------------------------------------------------------------------------

export interface HealthResponse {
  status: string;
  version: string;
  db: 'ok' | 'error' | string;
}

export interface ProviderStatus {
  provider: string;
  capability: string;
  ok: boolean;
  configured: boolean;
  message: string | null;
  checked_at: string | null;
}

export interface JobRunRow {
  job_name: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  detail: string | null;
}

export interface DataHealthResponse {
  providers: ProviderStatus[];
  jobs: JobRunRow[];
  data: {
    instruments: number;
    last_bar_date: string | null;
    last_run_at: string | null;
    price_staleness_days: number | null;
  };
}

/** Sanitised settings from GET /api/config — shapes come from backend config. */
export interface AppConfig {
  universe?: Record<string, unknown>;
  horizons?: Record<string, unknown>;
  gates?: Record<string, unknown>;
  alert_policy?: Record<string, unknown>;
  risk_policy?: Record<string, unknown>;
  scan?: Record<string, unknown>;
  base_currency?: string;
  model_version?: string;
  [key: string]: unknown;
}

export interface ModelVersionRow {
  version: string;
  created_at: string;
  weights: Record<string, unknown>;
  config_hash: string;
  notes: string | null;
  active: boolean;
}

export interface AuditRow {
  at: string;
  actor: string;
  action: string;
  detail: string | null;
}

export interface ScanAccepted {
  run_id: number;
}

// ---------------------------------------------------------------------------
// Universe & companies
// ---------------------------------------------------------------------------

export interface Instrument {
  id: number;
  ticker: string;
  exchange: string;
  market: string;
  name: string;
  sector: string;
  industry: string;
  currency: string;
  security_type: string;
  is_active: boolean;
  delisted_at: string | null;
}

export interface CompanyLatest {
  run_id: number;
  as_of: string;
  best_fit_horizon: string | null;
  horizons: Partial<Record<Horizon, HorizonScore>>;
  warnings?: string[];
}

export interface CompanyDetail {
  instrument: Instrument;
  latest: CompanyLatest | null;
  warnings?: string[];
  liquidity: {
    market_cap_base: number | null;
    median_daily_traded_value_base: number | null;
    price_staleness_days: number | null;
  } | null;
  watchlisted: boolean;
  owned: boolean;
}

export interface PriceBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  adj_close: number;
  volume: number;
}

export interface PriceMarker {
  date: string;
  family: string;
  state: string;
  transition: string;
  alert_id: number;
}

export interface PricesResponse {
  bars: PriceBar[];
  markers: PriceMarker[];
}

export interface FinancialQuarter {
  period_end: string;
  published_at: string;
  revenue: number | null;
  gross_margin_pct: number | null;
  operating_margin_pct: number | null;
  net_income: number | null;
  eps_diluted: number | null;
  fcf: number | null;
  net_debt: number | null;
  is_restatement: boolean;
}

export interface FinancialsResponse {
  quarters: FinancialQuarter[];
}

export interface EngineView {
  engine: string;
  score: number | null;
  components: Record<string, number>;
  evidence: Evidence[];
  warnings: string[];
  data_quality: number;
  details?: Record<string, unknown>;
}

export interface EnginesResponse {
  engines: EngineView[];
  run_id?: number;
  as_of?: string;
}

export interface PeerRow {
  instrument_id: number;
  ticker: string;
  name: string;
  metrics: Record<string, number | null>;
}

export interface PeersResponse {
  peers: PeerRow[];
}

// ---------------------------------------------------------------------------
// Scores & opportunities
// ---------------------------------------------------------------------------

export interface ScoreRunRow {
  id: number;
  run_at: string;
  as_of: string;
  model_version: string;
  trigger: string;
  universe_size: number;
  scored: number;
  abstained: number;
  status: string;
  detail: string | null;
}

export interface ActiveSignalTag {
  family: string;
  state: string;
}

export interface OpportunityRow {
  instrument_id: number;
  ticker: string;
  name: string;
  market: string;
  sector: string;
  horizon: Horizon;
  opportunity: number;
  confidence: number;
  risk: number;
  components: Record<string, number>;
  best_fit_horizon: string | null;
  gate_passed: boolean;
  abstained: boolean;
  active_signals: ActiveSignalTag[];
  market_cap_base: number | null;
  owned: boolean;
  watchlisted: boolean;
}

export interface OpportunitiesResponse {
  as_of: string | null;
  run_id: number | null;
  items: OpportunityRow[];
  total: number;
}

export interface OpportunityFilters {
  horizon?: Horizon;
  market?: string;
  sector?: string;
  family?: string;
  min_opportunity?: number;
  min_confidence?: number;
  max_risk?: number;
  gated_only?: boolean;
  owned?: boolean;
  watchlisted?: boolean;
  catalyst_within_days?: number;
  limit?: number;
  offset?: number;
}

// ---------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------

export interface AlertSummary {
  id: number;
  created_at: string;
  as_of: string;
  instrument_id: number;
  ticker: string;
  name: string;
  family: string;
  lifecycle_state: string;
  transition: string;
  horizon: Horizon;
  priority: string;
  title: string;
  read: boolean;
  opportunity: number | null;
  confidence: number | null;
  risk: number | null;
  thesis_summary: string;
}

// ---- AlertPayload (mirrors vigil/backend/src/vigil/schemas/alerts.py) -----

export interface CompanyHeader {
  name: string;
  ticker: string;
  exchange: string;
  market: string;
  sector: string;
  industry: string;
  market_cap_local: number | null;
  market_cap_base: number | null;
  base_currency: string;
  local_currency: string;
}

export interface PriceStamp {
  price: number;
  currency: string;
  as_of_date: string;
  /** Exact market-data timestamp of the last bar. */
  bar_timestamp: string;
  staleness_trading_days: number;
  fx_to_base?: number | null;
  fx_as_of?: string | null;
}

export interface ScoreView {
  horizon: string;
  opportunity: number;
  confidence: number;
  risk: number;
  components: Record<string, number>;
  explanation: string[];
}

export interface ThesisQA {
  why_this_company: string;
  why_now: string;
  what_market_misunderstands: string;
  what_would_prove_wrong: string;
  expected_holding_period: string;
  early_trim_or_exit_causes: string;
  three_largest_risks: string[];
}

export interface AnalystTargetBlock {
  mean?: number | null;
  implied_upside_pct?: number | null;
  count?: number | null;
  dispersion_pct?: number | null;
  median_age_days?: number | null;
  [key: string]: number | string | null | undefined;
}

export interface ValuationSummary {
  primary_multiple: string | null;
  multiples: Record<string, number>;
  vs_history_percentile: number | null;
  vs_peers_note: string | null;
  analyst_target: AnalystTargetBlock | null;
  fair_value_low: number | null;
  fair_value_high: number | null;
}

export interface SupportZone {
  low?: number;
  high?: number;
  kind?: string;
  strength?: number | string;
  touches?: number;
  [key: string]: unknown;
}

export interface TechnicalSummary {
  trend_state: string | null;
  support_zones: SupportZone[];
  resistance_levels: number[];
  rsi14: number | null;
  atr_pct: number | null;
  reward_risk: number | null;
  notes: string[];
}

export interface CatalystView {
  kind: string;
  date: string;
  days: number;
  confirmed: boolean;
  binary: boolean;
  description: string;
  priced_in_pct: number | null;
}

export interface SourceLine {
  provider: string;
  source_type: string;
  reference: string;
  published_at: string | null;
  freshness_days: number | null;
}

export interface ChangeSincePrevious {
  previous_alert_at: string | null;
  previous_state: string | null;
  opportunity_delta: number | null;
  risk_delta: number | null;
  price_change_pct: number | null;
  changed: string[];
}

export interface PriceBand {
  low: number;
  high: number;
}

export interface AlertPayload {
  company: CompanyHeader;
  signal_family: string;
  lifecycle_state: string;
  transition: string;
  best_fit_horizon: string | null;
  horizon: string;
  priority: string;

  price: PriceStamp;

  scores: ScoreView;
  all_horizons: Record<string, ScoreView>;

  change: ChangeSincePrevious;

  thesis: ThesisQA;
  thesis_summary: string;
  narrative_source: string; // "template" | "llm"

  supporting: Evidence[];
  contradicting: Evidence[];

  valuation: ValuationSummary;
  technicals: TechnicalSummary;
  catalysts: CatalystView[];

  entry_zone: PriceBand | null;
  conditions_before_entry: string[];
  invalidation_conditions: string[];
  fundamental_invalidation: string[];
  stop: number | null;
  scenarios: Scenario[];
  target_range: PriceBand | null;
  trim_conditions: string[];
  exit_conditions: string[];

  binary_event_warning: string | null;
  data_warnings: string[];
  missing_data: string[];

  sources: SourceLine[];
  model_version: string;
  generated_at: string;
  disclaimer: string;
}

export interface AlertDetailResponse extends AlertSummary {
  payload: AlertPayload;
}

// ---------------------------------------------------------------------------
// Signals
// ---------------------------------------------------------------------------

export interface StateHistoryEntry {
  state: string;
  as_of: string;
  reason?: string;
}

export interface SignalView {
  id: number;
  instrument_id: number;
  ticker: string;
  name: string;
  family: string;
  horizon: Horizon;
  state: string;
  created_at: string;
  updated_at: string;
  anchor_price: number | null;
  anchor_date: string | null;
  entry_plan: EntryPlan | null;
  last_scores: HorizonScore | null;
  state_history: StateHistoryEntry[];
  expires_at: string | null;
  active: boolean;
  last_alert_at: string | null;
}

// ---------------------------------------------------------------------------
// Portfolio & watchlist
// ---------------------------------------------------------------------------

export interface PositionRow {
  id: number;
  instrument_id: number;
  ticker: string;
  name: string;
  sector: string;
  quantity: number;
  avg_cost_local: number;
  currency: string;
  opened_at: string;
  last_price: number | null;
  value_base: number | null;
  weight_pct: number | null;
  unrealised_pct: number | null;
}

export interface PortfolioResponse {
  positions: PositionRow[];
  totals: {
    value_base: number | null;
    sector_weights: Record<string, number>;
    limits: {
      max_position_exposure_pct: number;
      max_sector_exposure_pct: number;
    };
    breaches: string[];
  };
}

export interface NewPosition {
  instrument_id: number;
  quantity: number;
  avg_cost_local: number;
  opened_at: string;
}

export interface WatchlistItem {
  id: number;
  instrument_id: number;
  ticker: string;
  name: string;
  added_at: string;
  notes: string | null;
}

// ---------------------------------------------------------------------------
// Calendar
// ---------------------------------------------------------------------------

export interface CalendarItem {
  instrument_id: number;
  ticker: string;
  name: string;
  kind: string;
  expected_date: string;
  days: number;
  date_confirmed: boolean;
  binary: boolean;
  description: string;
}

// ---------------------------------------------------------------------------
// Backtests
// ---------------------------------------------------------------------------

/** Combined trade-level + equity-curve metrics dict produced by the backend. */
export interface BacktestMetrics {
  n?: number;
  note?: string;
  open_at_end?: number;
  hit_rate?: number | null;
  hit_rate_ci95?: [number, number] | null;
  avg_return_pct?: number | null;
  median_return_pct?: number | null;
  avg_alpha_pct?: number | null;
  win_loss_ratio?: number | null;
  volatility_of_returns_pct?: number | null;
  avg_holding_days?: number | null;
  avg_mae_pct?: number | null;
  avg_mfe_pct?: number | null;
  exit_reasons?: Record<string, number>;
  total_return_pct?: number | null;
  cagr_pct?: number | null;
  annualised_vol_pct?: number | null;
  sharpe?: number | null;
  sortino?: number | null;
  max_drawdown_pct?: number | null;
  trades_per_year?: number | null;
  trading_days?: number;
  inconclusive?: boolean;
  [key: string]: unknown;
}

export interface ReliabilityBin {
  bin_low: number;
  bin_high: number;
  predicted: number;
  observed: number;
  count: number;
}

export interface CalibrationBlock {
  n: number;
  brier_score?: number;
  base_rate?: number;
  reliability?: ReliabilityBin[];
  definition?: string;
}

export interface BacktestSummaryRow {
  id: number;
  created_at: string;
  name: string;
  model_version: string;
  start_date: string;
  end_date: string | null;
  holdout_start: string | null;
  status: string;
  metrics: BacktestMetrics | null;
}

export interface BacktestTrade {
  instrument_id: number;
  ticker: string;
  family: string;
  horizon: string;
  signal_date: string;
  entry_date: string | null;
  entry_price: number | null;
  exit_date: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  holding_days: number | null;
  return_pct: number | null;
  benchmark_return_pct: number | null;
  mae_pct: number | null;
  mfe_pct: number | null;
  costs_bps: number | null;
  opportunity: number | null;
  confidence: number | null;
  risk: number | null;
}

export interface BacktestDetailResponse extends BacktestSummaryRow {
  by_bucket: Record<string, Record<string, BacktestMetrics>> | null;
  calibration: CalibrationBlock | null;
  trades: BacktestTrade[];
  notes?: string | null;
  detail?: string | null;
}

export interface NewBacktestRequest {
  name?: string;
  start: string;
  end?: string;
  holdout_start?: string;
  step_days?: number;
}

export interface BacktestAccepted {
  backtest_id: number;
}

// ---------------------------------------------------------------------------
// Notifications
// ---------------------------------------------------------------------------

export interface NotificationRow {
  id: number;
  alert_id: number | null;
  channel: string;
  created_at: string;
  status: string;
  detail: string | null;
}
