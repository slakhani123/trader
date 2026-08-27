# Vigil — personal equity research & alert platform

Vigil scans a configurable US/UK equity universe, scores every name on
three independent horizons (2–20 days, 1–6 months, 1–5 years), and issues
selective, evidence-backed research alerts — watch, buy-candidate, hold,
trim, exit, and thesis-invalidated — each with its full audit trail:
sources, publication timestamps, score explanations, entry/exit plans and
scenarios.

**Research support only.** Vigil contains no brokerage connectivity of any
kind and cannot place trades. Nothing it produces is a guarantee or
personalised financial advice.

```
providers (replaceable) ─▶ point-in-time store ─▶ snapshot(as_of)
        ─▶ 7 deterministic engines ─▶ versioned composite scoring
        ─▶ gates (abstain when weak) ─▶ signal rules ─▶ lifecycle FSM
        ─▶ immutable alerts (+ template/LLM narrative) ─▶ API ─▶ dashboard
```

## Quick start (zero external services)

```bash
cd backend
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

.venv/bin/vigil seed          # deterministic synthetic US/UK world (~15s)
.venv/bin/vigil scan          # full research scan -> scores, signals, alerts
.venv/bin/vigil alerts        # peek at what fired
.venv/bin/vigil serve         # API on :8000

# frontend
cd ../frontend && npm install && npm run dev   # dashboard on :5173

# point-in-time backtest with costs, delistings and lifecycle exits
cd ../backend && .venv/bin/vigil backtest --start 2024-01-02 --holdout-start 2026-01-01
```

Postgres-backed deployment: `cp .env.example .env`, set the passwords, and
`docker compose up` (db + api + scheduler + web). The SQLite path above is
the one verified end-to-end in development; see docs/LIMITATIONS.md.

## The demo world

22 fictional issuers (14 US, 8 UK) generated deterministically so every
mechanism has a worked example: a quality compounder, deep-value setups
with insider buying, a value trap that must NOT alert, a volume-confirmed
breakout, an oversold-quality correction, a parabolic social-driven mover,
a margin inflection, a deteriorating logistics name (guidance cut), a bank
and a REIT (sector-aware scoring), an illiquid microcap (filtered by
universe gates), a 4:1 split, a mid-history acquisition/delisting
(survivorship control), and a revenue restatement (point-in-time control).

**Real market data**: the demo runs on a synthetic world; switching to real
companies (free Stooq+EDGAR route, or EODHD) is a copy-two-files job —
see **docs/REAL_DATA.md**.

## Documentation

| Doc | What's in it |
|---|---|
| docs/ARCHITECTURE.md | system design, module map, core contract |
| docs/ASSUMPTIONS.md | every judgement call made where the brief was open |
| docs/ENGINE_SPEC.md | binding spec for the seven research engines |
| docs/API_SPEC.md | REST contract the frontend consumes |
| docs/FORMULAS.md | every indicator, score, gate and rule, with numbers |
| docs/PROVIDERS.md | adapter contract + how to add a data vendor |
| docs/BACKTESTING.md | bias controls, protocol, metrics, calibration loop |
| docs/LIMITATIONS.md | everything mocked, simplified or unavailable |
| docs/RUNBOOK.md | operations: scheduling, notifications, upgrades |

## Configuration

All research settings are env-configurable with documented defaults
(`backend/src/vigil/config.py`, overrides in `.env.example`): markets,
liquidity/market-cap/price floors, excluded industries, horizons, gates
(min confidence/opportunity/reward-risk/data quality), alert cooldowns and
material-change thresholds, risk tolerance and exposure limits, scan
schedule, backtest costs, base currency (GBP default), LLM narrative
on/off.

## Principles the code enforces

- Deterministic calculations; the optional LLM only narrates a structured
  evidence packet, and any unsupported numeric claim rejects its output.
- Every material claim carries a source reference, publication timestamp
  and freshness; alerts are immutable once issued (the paper track record).
- Weak or missing data lowers confidence or triggers abstention — never a
  padded guess. Analyst targets are evidence, never intrinsic value.
- Scores are ownership-blind; portfolio context only shapes hold/trim/exit
  stances and exposure warnings.
- Backtests share the live code path, include delisted names, respect
  publication dates, and charge costs and execution delay.
