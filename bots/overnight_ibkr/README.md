# overnight_ibkr — MU close→open bot for Interactive Brokers

Buys at the Nasdaq **Closing Cross** (MOC) and sells at the next
morning's **Opening Cross** (resting OPG order), the exact prices the
strategy's edge was measured on. One symbol or a basket. Whole shares
only. Standalone module: shares no code with the Vigil platform.

Built from the research in `research/overnight_intraday/` — read
`REPORT.md` (the edge, its tails, the −64% year) and `live_costs.md`
(why IBKR Pro **Tiered**, ~$185/yr of friction) before running this.

**This bot places real orders with real money. It is provided as
research tooling, not financial advice. Pre-registered kill criteria
are configured in `config.toml` — set them before the first run, not
after the first drawdown.**

## How it trades

```
~15:15–15:45 ET   buy: place MOC BUY  ──►  16:00 closing cross fill
   immediately    place OPG SELL (rests overnight at IBKR)
09:30 ET next day resting sell executes in the opening cross
~09:50 ET         reconcile: record fill, P&L, evaluate kill criteria
~09:05 ET         sell: safety net — only acts if the resting sell is missing
```

Design properties:

* **Idempotent** — one night per symbol per date (SQLite journal);
  re-running a command never double-orders.
* **Journal-first on every order** — the intent (including an
  attempt-unique `orderRef`) is written *before* anything is
  transmitted, so a crash at any instant leaves a blocking journal row,
  never an order the journal has not heard of. Recovery paths adopt a
  resting order found at the broker instead of placing a second one —
  the classic duplicated-sell → naked-short failure is structurally
  excluded.
* **Honest booking** — a night is only CLOSED on a fill whose quantity
  matches and whose price is positive; partial or ambiguous outcomes are
  escalated loudly, never silently booked (they would corrupt the P&L
  the kill criteria run on).
* **Never stacks** — an unsold overnight position blocks new buys and
  alerts instead.
* **Sells only what it bought** — sell quantity is the journalled fill,
  capped by the actual account position; long-term holdings of the same
  ticker in the account are never touched (still: a dedicated account
  or sub-account is cleaner).
* **Calendar-aware** — full US holiday/early-close rules computed
  internally; when connected it also reads today's actual session hours
  from IBKR contract details (catches unscheduled closures). Half-days
  move the MOC deadline to ~12:50 ET automatically.
* **Halts itself** — kill criteria (consecutive losses, trailing-window
  P&L, drawdown vs nightly notional) are checked before every buy and
  at every reconcile; a trip requires a manual `resume`.
* **No market-data subscription needed** — sizing uses free
  delayed-frozen quotes; MOC/OPG are submit-blind.

## Setup

1. **IBKR account** (UK: IBKR Pro): switch pricing to **Tiered**
   (Settings → Account Settings → Fee structure — Fixed's $1 minimum
   doubles the cost at this size). **Margin** account type avoids
   cash-account good-faith-violation ambiguity in the daily T+1
   recycle; the bot never actually borrows. Fund and convert GBP→USD
   once on IDEALPRO.
2. **IB Gateway** on an always-on machine, logged into the live
   account, API enabled (Configure → Settings → API → Enable
   ActiveX/Socket Clients; port 4001; add 127.0.0.1 to trusted IPs;
   *disable* Read-Only API). For unattended restarts use
   [IBC](https://github.com/IbcAlpha/IBC) with the Gateway's
   auto-restart setting.
3. **Bot**:

   ```bash
   cd bots/overnight_ibkr
   python3 -m pip install -r requirements.txt   # ib_async
   cp config.example.toml config.toml           # edit symbols/clip/kill
   python3 -m unittest discover -s tests        # 37 tests, no broker needed
   python3 overnight_bot.py check               # connect + sanity print
   python3 overnight_bot.py buy --dry-run       # full logic, no orders
   ```

4. **Schedule** — copy `crontab.example` (`crontab -e`). It fires each
   leg at several London times because the bot gates itself on US
   Eastern time internally; wrong firings exit silently. This also
   covers the ~3 weeks/yr of GMT/EDT mismatch and 13:00 ET half-days.
   The evening reconcile rows matter: IBKR serves execution records
   same-day only, so that run is what recovers a fill if the buy
   process crashed mid-wait.

   **Operational invariants:** one host, one journal, one `client_id` —
   forever. The `flock` in the cron lines serializes same-host runs;
   nothing protects two hosts or two journals. Never change `client_id`
   once live (resting orders re-bind to it), and never use TWS's
   "Reset API order ID sequence". Set `notify_url` (a free
   [ntfy.sh](https://ntfy.sh) topic works) — without it, critical
   alerts exist only in `bot.log`.

5. **Go live small.** First month at 1 share/day (≈$15 of total
   friction) and compare journalled fills with the official
   opening/closing prints — that measures your real round-trip cost.
   Then scale `clip_usd`.

## Commands

| command | when | what |
|---|---|---|
| `buy` | afternoon window | MOC buy → wait fill → place resting OPG sell |
| `reconcile` | after the open | record sell fill, P&L, kill criteria |
| `sell` | before 09:28 ET | fallback: re-place a missing OPG sell |
| `status` | any time | journal summary, no connection needed |
| `check` | any time | connection/account/price sanity |
| `halt` / `resume` | manual | disable / re-enable buying |

Flags: `--config PATH`, `--dry-run`, `--force` (bypass the time-window
gate for manual catch-ups).

Exit codes: `0` ok · `1` blocked/degraded · `2` needs attention. Wire
`notify_url` (e.g. an [ntfy.sh](https://ntfy.sh) topic) to get pushed
alerts for every ERROR-level event.

## Failure playbook

| symptom | meaning | action |
|---|---|---|
| `unsold overnight position … not buying` | last night's exit failed | `reconcile`, then `sell` before 09:28 ET; investigate before resuming |
| `OPG sell REJECTED` | sell didn't rest after the buy | the reconcile/sell crons retry with a fresh attempt ref; if all fail, close manually |
| status `PLACING_SELL` | crash mid-placement; a sell *may* be resting | `reconcile` — it adopts the resting order if one exists, else reverts to HELD and retries; never places a duplicate |
| `past MOO cutoff with an unsold position` | both sell paths missed | sell manually at market; `reconcile` records it via executions if done same-day, else mark the night manually |
| `recovered by POSITION … P&L is an ESTIMATE` | buy fill found via position after executions expired | correct that night's `buy_fill` in the journal from your IBKR statement |
| `NOT auto-booking … partial` | sell partially filled then died | resolve manually; partial exits are deliberately not auto-managed |
| `KILL CRITERIA TRIPPED` | pre-registered stop hit | that was the plan — review `status` and the research before any `resume` |
| Gateway login lost | IBC restart failed / weekly re-auth | `check` fails loudly; orders already resting at IBKR are unaffected |

## Tax note (UK)

~250 disposals/yr under CGT with same-day and 30-day matching, each
computed in GBP at that day's rate. The journal (`overnight.sqlite`)
holds every fill and commission — export it for your tax software. See
`research/overnight_intraday/live_costs.md`.
