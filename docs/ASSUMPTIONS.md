# Assumptions

Decisions made where the brief was ambiguous, with rationale. Each is the
safest reasonable choice and is reversible.

## Repository placement

Vigil lives at the root of the dedicated `trader` repository (it was
originally scaffolded inside the `SME` repository on branch
`claude/stock-research-alert-platform-eskpbu`, then moved here at the
owner's request; that branch can be deleted). `backend/` and `frontend/`
are independent deployables sharing this repo.

## Product name

The brief left `[PRODUCT NAME]` open. The platform is called **Vigil** — a
personal equity research and alert platform. The name is used only for this
private tool and does not imitate any existing research vendor's branding.

## Data providers

- No market-data API keys were supplied, and the brief forbids scraping in
  violation of site terms. Version 1 therefore ships with:
  - A **deterministic synthetic provider** (seeded, reproducible) that
    generates a realistic multi-year US/UK universe — prices, corporate
    actions (splits, dividends, one acquisition delisting), point-in-time
    quarterly fundamentals with publication lags and one restatement,
    analyst estimates with revision history, news with source typing,
    catalysts, short interest, insider transactions, FX, and macro series.
    Everything downstream (engines, scoring, alerts, backtests, UI) runs on
    it end to end.
  - **Real provider adapters** for SEC EDGAR company facts/filings and
    Stooq end-of-day prices (both free, no key, terms-compatible), plus a
    typed stub layout showing exactly how to add a keyed vendor (Polygon,
    Tiingo, EODHD, etc.). Adapters not configured report themselves as
    `unavailable` in Data Health rather than silently degrading.
- Options-derived data (IV, skew, unusual activity) has no free
  terms-compatible source; the adapter interface exists and the UI marks it
  "unavailable with the selected provider", per the brief.
- Earnings-call transcripts require licensing; the schema supports them, no
  provider ships them in v1.

## Database

- Production target is **PostgreSQL** (docker-compose provided). The code
  uses SQLAlchemy 2.x with types portable to SQLite, and the test suite,
  demo seed, and CI run on SQLite so the repo works with zero external
  services. The Docker daemon is not available in the build sandbox, so the
  compose file is provided and documented but was not executed here; the
  same Alembic migrations run on both engines.
- TimescaleDB was evaluated and **not** adopted for v1: the daily-bar volume
  of a personal universe (thousands of rows/day) does not justify the
  operational surface. The price table is keyed and indexed so a later
  Timescale hypertable conversion is a migration, not a redesign.

## Scheduling and jobs

APScheduler in-process (end-of-day scan by default, optional intraday
interval) instead of Celery/Redis: a personal single-user tool does not
justify a broker. Jobs are idempotent and journaled in `job_runs` so a
missed run is visible and re-runnable. The scheduler interface is small and
documented so a queue can replace it later.

## Authentication

Single-user bearer-token auth (`VIGIL_API_TOKEN`, hash-compared). Suitable
for a private personal tool behind TLS; multi-user auth/OIDC is out of
scope for v1 and noted in LIMITATIONS.

## LLM usage

- The LLM (Anthropic API, key optional) only turns a **structured evidence
  packet** into narrative fields, validated against a strict schema. Every
  number in the narrative must match a packet value (tolerance-checked);
  violations reject the narrative.
- Without an API key, a **deterministic template composer** produces the
  narrative from the same packet, so alerts are complete without any LLM.

## Sentiment

Deterministic: provider-supplied labels and a small transparent lexicon
scorer with source-type weights, novelty, and time decay. No LLM in the
sentiment *score* path (the LLM only narrates).

## Base currency and FX

GBP base. Instruments store local currency; report-level values convert at
the point-in-time FX rate with the FX timestamp shown. Scores are computed
in local currency (valuation ratios are currency-neutral).

## Backtesting costs

Defaults: 5 bps commission per side, half-spread slippage estimated from
price level and liquidity band, one-day execution delay (signals computed on
close, filled at next open). All configurable.

## Alert delivery

In-app first (required). Webhook delivery is implemented (generic JSON
POST). SMTP email is implemented but off until credentials are configured.
Mobile push is a documented stub (needs an APNs/FCM account).

## What is explicitly out of scope in v1

- Any brokerage/order connectivity (deliberately absent; no code path).
- Intraday tick data (the intraday monitor re-uses EOD logic on latest
  quotes when a provider supplies them).
- Multi-user tenancy.

See `docs/LIMITATIONS.md` for the full mocked/unavailable inventory.
