# Runbook

## Daily operation

The scheduler (`vigil schedule`, or the `scheduler` compose service) runs:

| Job | When (UTC, configurable) | What |
|---|---|---|
| eod_scan | 21:00 weekdays | incremental ingest → full scan → alert delivery → watch expiry |
| daily_digest | 07:00 | ranked digest of gate-passing names + last-24h alerts |
| weekly_review | Sunday | thesis review of all active signals |
| catalyst_reminders | daily | upcoming events for owned/watchlisted/signalled names |
| intraday_monitor | every 30m (only when `VIGIL_SCAN__SCAN_FREQUENCY=intraday`) | re-scan on latest data |

Every job writes a `job_runs` row; failures raise an in-app + webhook
data-failure notification. A missed window (process down) is visible in
Data Health — re-run manually with `vigil scan`.

## Manual commands

```bash
vigil seed            # (re-)ingest configured providers; idempotent
vigil scan --as-of 2026-08-25
vigil alerts --limit 20
vigil health          # provider/capability status
vigil backtest --start 2024-01-02 --end 2026-08-25 --holdout-start 2026-01-01
```

## Database

- Dev/tests: SQLite (`VIGIL_DATABASE_URL=sqlite:///vigil.db`).
- Production: Postgres via compose; migrations run automatically on API
  start (`alembic upgrade head`). New schema change → `alembic revision
  --autogenerate -m "..."` and commit the migration.

## Upgrading the scoring model

1. Add a new version to `scoring/weights.py` (never edit an existing one).
2. Set `VIGIL_SCORING_MODEL_VERSION` to the new version.
3. The next scan registers it in `model_versions` and every score/alert
   records it. Old alerts keep their original version — the audit trail
   stays intact.

## Secrets

All secrets live in `.env` (never committed): API token, provider keys,
SMTP, Anthropic key. Rotate by editing `.env` and restarting.

## Health checks

- `GET /api/health` — liveness (DB round-trip).
- `GET /api/health/data` — provider capability status, job history, data
  freshness. The dashboard's Data Health page renders this.

## Backups

State worth backing up: the Postgres volume (`vigil_pgdata`) or the SQLite
file. Alerts/signals/backtests are the irreplaceable audit trail; raw
payloads make full re-normalisation possible.
