# Switching to real market data

Two routes. Both keep the universe as an editable list you control
(`backend/universe.yml`). All commands below are for Windows PowerShell,
run from the `backend` folder (`cd $HOME\Documents\trader\backend`);
Mac/Linux users swap `.venv\Scripts\` for `.venv/bin/` and `Copy-Item`
for `cp`.

## Route 1 — Free (Stooq prices + SEC EDGAR US financials)

What you get: real daily prices for US and UK names, real quarterly
financial statements for US names, real USD/GBP FX, VIX. What stays off:
analyst estimates, news/sentiment, catalyst calendars, UK financials —
those engines abstain and confidence is honestly lower.

```powershell
git pull                                          # get the latest code first (from trader folder)
cd backend
Copy-Item universe.example.yml universe.yml       # your editable company list
Copy-Item env.realdata.example .env               # the free-data configuration
notepad .env                                      # put YOUR name+email on the EDGAR line
Remove-Item vigil.db                              # start clean (demo data out)
.venv\Scripts\vigil seed                          # fetch ~6 years of real data (several minutes)
.venv\Scripts\vigil scan
.venv\Scripts\vigil health                        # every capability's honest status
$env:VIGIL_DEBUG = "true"; .venv\Scripts\vigil serve
```

Notes:
- `seed` is polite to the free services (~1 request/second) — a 40-name
  universe takes a few minutes the first time. Re-running only fetches
  what's new.
- The SEC **requires** a real name and email in `VIGIL_EDGAR_USER_AGENT`;
  without it the fundamentals capability reports itself unavailable.
- Stooq serves split-adjusted prices without corporate-action detail: fine
  for live scanning, but treat backtests over split events with caution
  (docs/LIMITATIONS.md). EODHD fixes this properly.
- Expect FEWER alerts than the demo: with estimates/news off, several
  signal families can't confirm, and the "minimum engines reporting" gate
  (default 4) means UK names — 3 engines on the free route — surface as
  scored-but-ungated rather than alerting. That is the abstention
  principle working as designed.

## Route 1b — Tiingo (free API key) when stooq won't cooperate

Stooq needs no signup but blocks some networks (corporate firewalls, VPNs)
and enforces a daily download cap. Tiingo is the reliable free
alternative for US prices — and it serves proper per-day split/dividend
data, which is better point-in-time hygiene than stooq. US-only on the
free tier; UK names wait for EODHD.

```powershell
# after creating a free account at tiingo.com and copying your API token:
cd backend
Copy-Item env.tiingo.example .env
notepad .env                 # paste your token + your name/email for EDGAR
notepad universe.yml         # swap ^SPX row for SPY (see env.tiingo.example),
                             # comment out the ^UKX row and UK companies
Remove-Item vigil.db
.venv\Scripts\vigil probe AAPL   # should say OK with bar counts
.venv\Scripts\vigil seed
.venv\Scripts\vigil scan
```

## Route 2 — EODHD (paid, full coverage)

Adds: corporate actions, UK/LSE financial statements, analyst estimates
with revision history fields, price targets, news, earnings calendar.

```powershell
# after subscribing at eodhd.com and copying your API token:
cd backend
Copy-Item universe.example.yml universe.yml       # if not already done
Copy-Item env.eodhd.example .env
notepad .env                                      # paste your token
Remove-Item vigil.db
.venv\Scripts\vigil seed
.venv\Scripts\vigil scan
```

Caveats (also in docs/LIMITATIONS.md):
- The adapter follows EODHD's documented API but was **not verified against
  the live service** during development. Parsers fail soft: a changed field
  name shows up as a warning in `vigil health` / the Data Health page, not
  a crash. Report anything red and it's a small fix.
- Estimates/targets are current snapshots; a true point-in-time estimate
  history builds up from your own daily scheduled ingests. Backtests over
  earlier dates should not lean on estimate-driven signals until that
  history exists.
- Short interest and insider transactions are not mapped in v1.

## Editing your universe

Open `backend\universe.yml` in Notepad. Each line is one company; UK
tickers end `.L`; keep the two `security_type: index` benchmark entries.
`sector` controls which companies get compared as peers. After editing:
`.venv\Scripts\vigil seed` then `.venv\Scripts\vigil scan`.

## Switching back to the demo world

```powershell
Remove-Item .env, vigil.db
.venv\Scripts\vigil seed
.venv\Scripts\vigil scan
```

## Daily operation with real data

Leave the scheduler running instead of scanning by hand:

```powershell
.venv\Scripts\vigil schedule
```

It ingests fresh data and scans each weekday evening (21:00 UTC by
default), sends the daily digest, weekly thesis review and catalyst
reminders, and records every run under Data Health.
