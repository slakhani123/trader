# Pre-release review record (2026-09-07)

Two independent hostile reviews ran before release: an order-safety
review that traced crash windows by executing probe tests against the
real code, and an API-correctness review verified against the ib_async
2.1.0 source and IBKR's own documentation.

## Design assumptions verified

* **Resting OPG orders survive everything relevant.** Orders transmitted
  with `transmit=True` live at IBKR's servers; API disconnects, the
  Gateway's nightly restart, and IBKR's server reset do not cancel them.
  IBKR's own docs: an OPG order is "held in the system and submitted at
  the next day's open". The 16:05-ET resting sell is correct usage.
* Order construction is correct per the official TWS API samples:
  MOC = `orderType='MOC'` (DAY tif), MOO = `'MKT'` + `tif='OPG'`; SMART
  routing accepted; no extra flags needed.
* Execution records are served **same-day only** — the evening reconcile
  cron exists because of this.
* `orderId` is unique only per clientId; recovery therefore matches by
  the bot's deterministic `orderRef` tag (and clientId-filtered ids as a
  fallback), never by bare orderId.

## Findings and dispositions (all fixed unless noted)

**Critical**
1. *Duplicated OPG sell → naked short*: a crash (or exception after
   transmit) between placing the sell and journaling it let the next
   morning's fallback place a second full-size sell; both filled and the
   shared orderRef made reconcile book it as a clean night. **Fixed**:
   journal-first `PLACING_SELL` state with attempt-unique refs
   (`:S1`, `:S2`…), try/except around both placement sites, and every
   recovery path now adopts a resting sell found at the broker before it
   may place one. Regression tests cover the exact scenario.
2. *Fabricated 'Filled' status from execution records* let a
   partial-fill-then-cancel be booked as a full exit (wrong P&L feeding
   the kill criteria, orphaned shares). **Fixed**: execution recovery
   returns status `Executions` with the executed quantity; a night is
   only CLOSED when quantity matches and price > 0; partials are
   escalated as ERROR + alert, never auto-booked.

**Major**
3. *Cancelled-unfilled resting sell invisible in a fresh session* looped
   "state unknown" forever. **Fixed**: past the exit open, reconcile
   distinguishes still-resting / vanished-with-position (→ HELD + alert,
   fallback re-sells) / flat-but-unbooked (→ ERROR + alert).
4. *Crash during the fill wait, executions expired overnight* left
   PENDING_BUY stuck while shares sat unhedged. **Fixed**: position-based
   recovery promotes the night to HELD with an estimated fill price
   (loudly marked ESTIMATE) so the exit is protected; plus the evening
   reconcile cron recovers the true fill same-day in most cases.
5. *P&L booked on the journalled qty when fewer shares were sold.*
   **Fixed** by the quantity-match rule in (2) and by the fallback
   journaling its qty reduction before selling.
6. *avgFillPrice=0 status race* could book a fake ±100% night. **Fixed**:
   zero-price fills are never journalled (`good_fill`), the broker
   adapter re-polls after Filled until price and commission arrive.
7. *CWD-relative journal path* could silently open a fresh journal (and
   forget the halt flag). **Fixed**: resolved relative to the config file.
8. *Walking away from a working order at the wait deadline.* **Fixed**:
   the adapter cancels on deadline and reports the post-cancel truth
   (handling a cancel/fill race).

**Minor (fixed)**: basket kill-criteria drawdown now grouped per date
(was overstated k-fold for k symbols); `qualifyContracts` `[None]`
result detected; commission NaN-guard replaced with execId presence and
a post-fill wait; delayed snapshot polled up to ~12 s; unscheduled
full-day closures (liquidHours `CLOSED`) skip the buy; morning
reconcile places a missing exit itself instead of relying on cron
ordering; connect failures explain the duplicate-clientId case; cron
lines use `flock`; empty `notify_url` warns at startup and delivery
failures are printed.

**Accepted / documented, not coded around**
* Two hosts or two journals defeat every journal-based guard — the bot
  is single-host by contract (README, crontab flock).
* Partial exits are deliberately manual: MOC/MOO market orders on liquid
  names essentially never partial-fill, and silent auto-handling was the
  riskier branch.
* A same-day crash where the buy filled *and* the host stays down past
  midnight leaves an ESTIMATE-priced night (finding 4's residual); the
  journal marks it and the operator corrects it from the statement.

52 unit tests cover the strategy core, including regression tests for
every critical/major finding above.
