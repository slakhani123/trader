# Vigil — Architecture

A personal equity research and alert platform. Deterministic calculation
core, point-in-time data throughout, optional LLM strictly for narrative
synthesis. **No brokerage connectivity exists anywhere in the codebase.**

```
                          ┌──────────────────────────────────────────────┐
 Providers (replaceable)  │                Backend (Python)              │
 ┌─────────────────┐      │                                              │
 │ synthetic (demo)│──┐   │  ingest ─▶ normalised PIT store (Postgres)   │
 │ SEC EDGAR       │──┼──▶│     raw payloads + lineage + published_at    │
 │ stooq EOD       │──┘   │                                              │
 │ <your vendor>   │      │  snapshot.build(instrument, as_of)           │
 └─────────────────┘      │     └─ the ONLY read path engines see        │
                          │                                              │
                          │  7 engines (pure, deterministic)             │
                          │   quality · valuation · technical · momentum │
                          │   sentiment · catalyst · regime              │
                          │     each → EngineResult{score, evidence[]}   │
                          │                                              │
                          │  composite scorer (versioned weights)        │
                          │   → per-horizon Opportunity/Confidence/Risk  │
                          │  gates → abstain | candidate                 │
                          │  signal rules → lifecycle state machine      │
                          │  alert builder → immutable alert + packet    │
                          │  narrative: LLM(strict schema) | templates   │
                          │                                              │
                          │  FastAPI ◀── scheduler (EOD scan, digests)   │
                          └──────────┬───────────────────────────────────┘
                                     │ REST + bearer token
                          ┌──────────▼───────────────┐
                          │  Frontend (React + TS)    │
                          │  dashboards · company page│
                          │  alerts · backtests       │
                          └──────────────────────────┘
```

## Layout

```
.  (repo root)
  backend/
    src/vigil/
      config.py            # pydantic-settings; every tunable documented
      db.py                # SQLAlchemy engine/session (Postgres or SQLite)
      models/              # ORM: reference, market, fundamentals, events,
                           #      scoring, signals, portfolio, backtest, ops
      schemas/             # core.py = THE CONTRACT (see below), api.py
      providers/           # base protocols, registry, synthetic/, edgar.py,
                           #      stooq.py, template.py (how to add one)
      data/                # ingest, snapshot (PIT gateway), quality, fx
      indicators/          # ta.py, stats.py — pure numpy/pandas
      engines/             # the 7 engines, one module each + base.py
      scoring/             # weights (versioned), composite, confidence, gates
      signals/             # family rules, lifecycle FSM, dedup/cooldown
      alerts/              # builder, notify (in-app, webhook, email)
      llm/                 # packet, synthesiser, validator, template fallback
      backtest/            # PIT engine, costs, metrics, calibration
      jobs/                # scheduler, scan, seed_demo, digests
      api/                 # FastAPI app + routers
      cli.py               # vigil seed|scan|serve|backtest|...
    tests/                 # unit, integration, contract, e2e
    alembic/               # migrations
  frontend/                # Vite + React + TS
  docker-compose.yml       # postgres + api + web
  docs/                    # this file, ASSUMPTIONS, FORMULAS, PROVIDERS,
                           # RUNBOOK, LIMITATIONS, BACKTESTING
```

## The core contract (`schemas/core.py`)

Everything hangs off four types:

- **`SourceRef`** — provider, source type, native id/url, `published_at`,
  `retrieved_at`, `freshness_days`. Every evidence item carries one.
- **`Evidence`** — machine key, human statement (deterministic template),
  value, direction (`supports|contradicts|neutral`), pillar, `SourceRef`,
  `as_of`. Alerts and narratives may only reference these.
- **`InstrumentSnapshot`** — the frozen point-in-time bundle handed to
  engines: price history ≤ as_of, fundamentals with `published_at ≤ as_of`
  (restatements applied only when published), estimates/targets as-of, news,
  catalysts, short interest, insiders, peers, benchmark + sector series,
  macro, FX, liquidity stats, data-quality flags. Built by
  `data/snapshot.py`, which is the *only* way engines read data. Look-ahead
  prevention lives here, in one place, and is tested there.
- **`EngineResult`** — engine name, score `0–10 | None` (None = abstain),
  sub-component scores, `evidence[]`, `warnings[]`, `data_quality 0–1`.

Engines are pure functions `analyse(snapshot, config) -> EngineResult` with
no I/O and no clock access — fully reproducible from a snapshot.

## Scoring

- `scoring/weights.py` holds **versioned** per-horizon, per-strategy weight
  tables (also serialised into the `model_versions` table with a config
  hash; every score row records the version that produced it).
- Opportunity = weighted engine blend per horizon; Confidence is computed
  (not averaged) from data quality, evidence agreement, coverage, indicator
  concentration, liquidity, binary-event proximity and calibration history;
  Risk from volatility/drawdown/leverage/event/regime inputs. All 0.0–10.0.
- `scoring/gates.py`: configurable minimum confidence, liquidity,
  reward/risk and data-quality gates. Failing a gate ⇒ abstention (recorded
  with reasons), never a weak alert.

## Signals and alerts

- `signals/rules.py`: one deterministic rule per family (Deep Value,
  Quality Compounder, Oversold-at-Support, Constructive Pullback, Breakout,
  Fundamental Inflection, Estimate-Revision Momentum, Watch, Hold, Avoid,
  Trim, Full Exit, Thesis Invalidated). Rules consume scores + evidence and
  emit candidates with entry zone, invalidation, targets, scenarios.
- `signals/lifecycle.py`: `WATCHING → TRIGGERED → REINFORCED → WEAKENING →
  TRIM → EXITED|INVALIDATED|EXPIRED` with explicit transition guards,
  cooldowns, and material-change tests (score delta, price move, state
  change, new catalyst, risk change) before any re-alert.
- `alerts/builder.py` produces the full alert payload of the brief
  (thesis Q&A, supporting/contradicting evidence, zones, stops, scenarios,
  trim/exit conditions, sources, freshness, disclaimer) and stores it
  **immutably**; shadow/paper mode is simply "alerts are never rewritten".

## Backtesting

`backtest/engine.py` replays history by calling the same snapshot builder
and engines at each historical date (no separate formula copy), includes
delisted names, applies publication lags, corporate-action-correct prices,
costs/slippage/delay, walk-forward splits with an untouched holdout, and
reports per strategy/horizon/regime/cap-band/score-bucket with hit rate,
alpha vs matched benchmark, Sharpe/Sortino, MAE/MFE, turnover, reliability
curves and Brier scores. Lifecycle (entry→trim/exit) is tested end to end.

## Versioning & audit

`model_versions` (weights + formula hash + notes), `score_runs` (config
hash, universe, timings), `job_runs`, `audit_log`. A score is reproducible
from (model_version, snapshot date, instrument).

## Phased delivery

1. **Spine** — schemas/models/providers/synthetic data/snapshot/indicators.
2. **Engines** — the 7 engines + unit tests (parallel work).
3. **Scoring & signals** — composite, gates, lifecycle, alert builder.
4. **Services** — API, scheduler, notifications, LLM narrative, backtester.
5. **Frontend** — dashboards, company page, calendars, health, audit.
6. **Integration** — seed → scan → alerts → UI; full test run; docs; CI.
