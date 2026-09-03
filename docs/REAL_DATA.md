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

## Reading `vigil health`

The provider rows describe the LAST ingest run; the `store contents` block
underneath is what the database actually holds. Re-running `seed` is safe
and idempotent — a second run reports rows as "already in store" rather
than re-inserting them, so "0 new reports (487 already in store)" is a
healthy store, not a failure. Only "0 new … (0 already in store)" means a
capability truly has no data.

Two other things that look like errors but aren't:
- `vigil serve` logging `GET / -> 404`: the API has no home page. The
  dashboard is the separate frontend (`npm run dev` in the `frontend`
  folder, then open http://localhost:5173).
- `vigil probe TICKER` now also tests fundamentals — run it whenever a
  capability looks wrong and paste the output when reporting a problem.

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

## Backtesting on the free data tier

Expect **few or zero trades**. Confidence is penalised for every engine
without data, and with estimates/news/catalyst providers unconfigured the
buy gate's minimum confidence can be structurally out of reach (typical
ceiling ≈ 4.5 vs the default gate of 5.5) — some families additionally
require catalyst support that no free source provides. Watch-grade setups
still appear live, but a watch never trades. This is the abstention
principle: the system refuses to simulate conviction it doesn't have.

A zero-trade run now says exactly why on its detail page ("Why no
trades?"), with the blocking gate conditions counted. Your options:

- **Add richer data** (Route 2 / EODHD): estimates, news and catalysts give
  confidence room to clear the gate — the intended path.
- **Consciously relax gates for an experiment** in `.env`, e.g.
  `VIGIL_GATES__MIN_CONFIDENCE=4.0` (and re-run the backtest). Lower gates
  mean weaker-evidence signals; treat results as exploration, not
  validation, and remove the override afterwards.

## Windows one-click scripts

The repo root has three batch files you can double-click in File Explorer
(or run by name from PowerShell) instead of typing the individual commands:

- `update.cmd` — pulls the latest code, fetches new market data, runs a
  scan, and prints the health summary.
- `start.cmd` — opens the API and the dashboard in their own windows and
  opens http://localhost:5173 in your browser.
- `backtest.cmd` — runs the standard point-in-time backtest
  (2021-06-01 onward, final year held out).

## Daily operation with real data

Leave the scheduler running instead of scanning by hand:

```powershell
.venv\Scripts\vigil schedule
```

It ingests fresh data and scans each weekday evening (21:00 UTC by
default), sends the daily digest, weekly thesis review and catalyst
reminders, and records every run under Data Health.
