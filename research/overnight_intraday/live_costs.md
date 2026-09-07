# Cheapest live implementation (UK resident), September 2026

Question: cheapest way to run the MU close→open bot **live** from the UK.
Facts below verified via September-2026 sources (broker pricing pages,
API docs, SEC/FINRA fee schedules); open items flagged. Costs assume 252
round trips/yr; gross edge at the 2018–2026 pace is 15.4 bps/night
(= $389/yr per $1,000/day of clip).

## The one constraint that decides everything

The backtested edge is measured **auction print to auction print**
(Nasdaq Closing Cross → Opening Cross). MOC/MOO orders fill *at* those
prints; anything else (a 15:59 market order, a queued-at-open market
order through a PFOF wholesaler) executes in continuous trading and pays
an unmeasurable slippage against the very prices the edge was measured
on — the literature (Berkman et al.) says the open systematically fades
*after* the print, i.e. the slippage is likely adverse on the sell side.
So "cheapest" must mean cheapest **with auction access**, or it's a
different (worse) strategy.

Brokers accessible to UK residents with official API + US auction orders:

| route | auction orders | API | all-in friction/yr, $1k/day | $10k/day | open items |
|---|---|---|---|---|---|
| **IBKR Pro, Tiered** | MOC/LOC + MKT-tif=OPG | IB Gateway (TWS API) | **≈ $185** ($0.35 min ×2 + cross fees + SEC/TAF) | ≈ $235 | none — everything verified |
| IBKR Pro, Fixed | same | same | ≈ $510 ($1.00 min ×2) | ≈ $510 | dominated by Tiered |
| Alpaca **Elite** | OPG/CLS TIF | alpaca-py REST | ≈ $10 | **≈ $75** | $30k net-deposit gate; UK Elite enrollment **unconfirmed**; May-2026 fee-schedule wording ("0–3%/transaction" vs advertised $0.004/sh) **unresolved**; US broker, no FCA/FSCS |
| TradeStation Intl | OPG/CLO | Web API v3 | ≈ $2,520 ($5/trade intl) | ≈ $2,520 | dead on arrival |
| Firstrade | OPG/CLO | **no official API** | — | — | unusable unattended |

No auction access (explicitly cheap, implicitly not): Alpaca basic,
tastytrade, Trading 212 (live API market-orders-only in beta), Webull UK
(API is market/limit only), Robinhood UK (no equities API at all),
moomoo (no UK entity). Their explicit costs are ~$5–60/yr, but they
convert the strategy into "market order at 15:59 + queued market at
09:30", paying continuous-session slippage estimated at 2–6 bps/side —
$25–$300/yr per $1k of clip, unbounded on gap days, and unknowable
until measured. Cheap on paper, opaque in practice.

Leveraged wrappers, priced and killed:
* **CFD / spread bet** (IG, CMC): overnight financing = benchmark +2.5–3.5%
  → 6.2–7.2%/yr on notional (SOFR 3.66%, Sep 2026), charged every
  calendar night — exactly the nights this strategy holds. Gross edge is
  ~3.9%/yr → structurally negative before spreads. Spread betting's
  CGT-free status saves at most ~0.9%/yr vs a taxed cash account —
  financing costs ~6× that. IG's share-CFD commission ($15/side minimum)
  is separately fatal at this size.
* **CME single-stock futures** (relaunched July 2026, incl. 10-share
  Micros): the carry embedded in the basis ≈ SOFR on notional for the
  hours held (~2.7%/yr) plus new-market spreads — worse than the cash
  route.

## Verdict

**Cheapest reliable live model: IBKR Pro (UK), Tiered pricing, margin
account, whole shares, MOC buy + MOO sell, via IB Gateway.**

* ≈ **$185/yr of friction at any clip from $1k to ~$30k/day** — the $0.35
  per-order minimum binds until ~100 shares, so the strategy gets
  proportionally cheaper as the clip grows:
  * $1k/day: $389 gross − $185 ≈ **+$204/yr** (friction eats 47%)
  * $5k/day: $1,945 − $215 ≈ **+$1,730/yr** (11%)
  * $10k/day: $3,890 − $235 ≈ **+$3,655/yr** (6%)
  * Breakeven clip ≈ **$500/day** — below that, friction eats over half
    the expected edge.
* Fills are the official auction prints → live P&L is directly
  comparable to the backtest, trade by trade.
* No market-data subscription needed (MOC/MOO are submit-blind), no
  inactivity fee, no account minimum. One-time GBP→USD at 0.002%
  (min $2). Margin account avoids cash-account good-faith-violation
  ambiguity in the daily T+1 recycle (no actual leverage is used).
* Stack: IB Gateway + IBC (auto-login/restart) + `ib_async` (successor
  to the archived ib_insync), cron: submit MOC by ~15:45 ET, MOO
  (tif=OPG) any time before 09:28 ET. Nasdaq MOC cutoff 15:55 ET.
  Infra $0 (any always-on machine) to ~$60/yr (small VPS).

**Cost floor, if two facts check out: Alpaca Elite** (~$75/yr at
$10k/day, ~$160/yr cheaper than IBKR). Requires $30k net deposits,
confirmation from Alpaca support that a UK-resident account can enroll
in Elite Smart Router, and resolution of the May-2026 fee-schedule
ambiguity. USD-only funding (use Wise/IBKR-style cheap conversion; their
Currencycloud path costs 1.5% capped $40). Worth one support email
before choosing; not worth blocking on.

**Where Alpaca Elite genuinely wins: the basket version.** Per-order
minimums make IBKR's cost linear in name count (~$180/yr per name), so
the 5-name semi basket (MU, TSM, MRVL, AMD, AVGO) costs ~$900/yr at
IBKR but ~$100/yr at Elite pricing. If the plan is the diversified leg
rather than MU alone, Elite's $30k gate buys a real saving.

## Sizing reality check (unchanged from the main report)

Cheapest ≠ good. At the recent-pace edge the friction-optimal setup
still nets ~$200/yr per $1k/day of clip, against −15.6% worst nights, a
−64% year (2022) and a −54% leg drawdown. A first live month at one
share/day costs ~$15 of friction and produces the one number no
research can: your realized fill-vs-print slippage.
