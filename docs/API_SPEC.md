# API specification (v1)

Binding contract between `backend/src/vigil/api/` and the frontend.
All routes are prefixed `/api`. Auth: `Authorization: Bearer <VIGIL_API_TOKEN>`
on every route except `/api/health`; when `VIGIL_DEBUG=true` and no token is
configured, auth is skipped (local dev only). Errors: JSON
`{"detail": str}` with proper status codes. All timestamps ISO-8601 UTC.
Pagination: `limit` (default 50, max 500) + `offset`; responses that
paginate return `{"items": [...], "total": int}`.

## Health & ops

- `GET /api/health` → `{status:"ok", version, db:"ok"|"error"}` (no auth).
- `GET /api/health/data` → `{providers:[{provider, capability, ok, configured,
  message, checked_at}], jobs:[{job_name, started_at, finished_at, status,
  detail}] (last 20), data:{instruments:int, last_bar_date, last_run_at,
  price_staleness_days}}`.
- `GET /api/config` → sanitised settings (universe, horizons, gates,
  alert_policy, risk_policy, scan, base_currency, model_version). No secrets.
- `POST /api/scan {as_of?: date}` → 202 `{run_id}`; runs the scan in a
  background thread; poll `GET /api/runs/{id}`.
- `GET /api/model-versions` → `[{version, created_at, weights, config_hash,
  notes, active}]`.
- `GET /api/audit?limit=` → `{items:[{at, actor, action, detail}]}`.

## Universe & companies

- `GET /api/instruments?market=&sector=&q=&active=&limit=&offset=` →
  `{items:[{id, ticker, exchange, market, name, sector, industry, currency,
  security_type, is_active, delisted_at}], total}`. `q` matches ticker/name.
- `GET /api/companies/{id}` → header + latest assessment:
  `{instrument:{...as above}, latest:{run_id, as_of, best_fit_horizon,
  horizons:{short|medium|long:{opportunity, confidence, risk, components,
  abstained, abstain_reasons, gate, explanation}}, warnings:[...]},
  liquidity:{market_cap_base, median_daily_traded_value_base,
  price_staleness_days}, watchlisted: bool, owned: bool}`.
  404 if never scored — the header block still returns with `latest: null`.
- `GET /api/companies/{id}/prices?days=730` → `{bars:[{date, open, high,
  low, close, adj_close, volume}], markers:[{date, family, state, transition,
  alert_id}]}` (markers from this company's alerts).
- `GET /api/companies/{id}/financials` → `{quarters:[{period_end,
  published_at, revenue, gross_margin_pct, operating_margin_pct, net_income,
  eps_diluted, fcf, net_debt, is_restatement}]}` (restatement-folded,
  latest-visible view).
- `GET /api/companies/{id}/engines?run_id=` → latest (or given) run's
  `{engines:[{engine, score, components, evidence:[Evidence], warnings,
  data_quality, details}]}`.
- `GET /api/companies/{id}/peers` → `{peers:[{instrument_id, ticker, name,
  metrics:{...}}]}` from the latest snapshot logic.
- `GET /api/companies/{id}/alerts?limit=` → `{items:[AlertSummary]}`.
- `GET /api/companies/{id}/signals` → `{items:[SignalView]}` (all, newest first).

## Scores & opportunities

- `GET /api/runs?limit=` → `{items:[{id, run_at, as_of, model_version,
  trigger, universe_size, scored, abstained, status, detail}]}`.
- `GET /api/runs/{id}` → same single object.
- `GET /api/opportunities?horizon=short|medium|long&market=&sector=&family=&
  min_opportunity=&min_confidence=&max_risk=&gated_only=true&owned=&
  watchlisted=&catalyst_within_days=&limit=&offset=` →
  ranked from the LATEST completed run:
  `{as_of, run_id, items:[{instrument_id, ticker, name, market, sector,
  horizon, opportunity, confidence, risk, components, best_fit_horizon,
  gate_passed, abstained, active_signals:[{family, state}],
  market_cap_base, owned, watchlisted}], total}`.
  Sorted by opportunity desc; `gated_only` filters to gate-passing rows.

## Alerts

AlertSummary = `{id, created_at, as_of, instrument_id, ticker, name, family,
lifecycle_state, transition, horizon, priority, title, read,
opportunity, confidence, risk, thesis_summary}`.

- `GET /api/alerts?family=&state=&priority=&horizon=&unread_only=&
  instrument_id=&since=&limit=&offset=` → `{items:[AlertSummary], total}`
  (newest first).
- `GET /api/alerts/{id}` → `{...AlertSummary, payload: AlertPayload}` (the
  full stored payload, verbatim).
- `POST /api/alerts/{id}/read` / `POST /api/alerts/{id}/unread` → 200.

## Signals

SignalView = `{id, instrument_id, ticker, name, family, horizon, state,
created_at, updated_at, anchor_price, anchor_date, entry_plan, last_scores,
state_history, expires_at, active, last_alert_at}`.

- `GET /api/signals?state=&family=&active=&instrument_id=&limit=&offset=` →
  `{items:[SignalView], total}`.
- `GET /api/signals/{id}` → SignalView.

## Portfolio & watchlist

- `GET /api/portfolio` → `{positions:[{id, instrument_id, ticker, name,
  sector, quantity, avg_cost_local, currency, opened_at, last_price,
  value_base, weight_pct, unrealised_pct}], totals:{value_base,
  sector_weights:{sector: pct}, limits:{max_position_exposure_pct,
  max_sector_exposure_pct}, breaches:[str]}}`.
- `POST /api/portfolio {instrument_id, quantity, avg_cost_local, opened_at}`
  → 201 `{id}`; `DELETE /api/portfolio/{id}` → closes (sets inactive).
- `GET /api/watchlist` → `{items:[{id, instrument_id, ticker, name, added_at,
  notes}]}`; `POST /api/watchlist {instrument_id, notes?}` → 201;
  `DELETE /api/watchlist/{id}`.

## Calendar

- `GET /api/calendar?days=60&binary_only=false` → upcoming catalysts across
  the universe: `{items:[{instrument_id, ticker, name, kind, expected_date,
  days, date_confirmed, binary, description}]}` sorted by date.

## Backtests

- `GET /api/backtests` → `{items:[{id, created_at, name, model_version,
  start_date, end_date, holdout_start, status, metrics}]}`.
- `GET /api/backtests/{id}` → full row incl. `by_bucket`, `calibration`,
  and `trades:[{instrument_id, ticker, family, horizon, signal_date,
  entry_date, entry_price, exit_date, exit_price, exit_reason, holding_days,
  return_pct, benchmark_return_pct, mae_pct, mfe_pct, costs_bps,
  opportunity, confidence, risk}]` (capped 2000, newest first).
- `POST /api/backtests {name?, start, end?, holdout_start?, step_days?}` →
  202 `{backtest_id}`; runs in a background thread.

## Notifications

- `GET /api/notifications?limit=` → `{items:[{id, alert_id, channel,
  created_at, status, detail}]}` — delivery log incl. data-failure notices.

## Frontend deep links (used by notifications)

`/alerts/{alert_id}` must render the full alert page.
