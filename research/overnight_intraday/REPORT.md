# Overnight vs intraday returns: testing the viral Micron chart

**Date:** 2026-09-07 · **Author:** Vigil research (automated study)
**Code:** `study.py`, `extend_study.py`, `mu_deepdive.py` (stdlib-only, reproducible)
**Data:** three independently-collected GitHub datasets (Marjanovic
"Huge Stock Market Dataset" 1962–2017; yennanliu/finance_data 2016–2026-09,
nightly yfinance pulls; two smaller complements for AAPL/GOOGL/SPY). MU's
2026-09-04 close in the data ($1,016.59) matches live quotes exactly.

## The claim under test

A viral LinkedIn/X chart (Bruce Knuteson's methodology, overnightreturns.org;
re-rendered and mega-viral late Aug 2026) claims MU 1990→2025: cumulative
**overnight** (buy close, sell next open) ≈ **+138,330,342%** while cumulative
**intraday** (buy open, sell close) ≈ **−99.92%**. The poster proposes a bot:
buy $1,000 of MU at every close, sell at the next open.

## Verdict in one paragraph

**The pattern is real, but the strategy is not a money-printer.** MU's
overnight leg has beaten its intraday leg persistently for 30+ years,
including 2018–2026 on clean, live-verified data (≈ +15 bps/night, t≈3.3).
The headline cumulative percentages, however, are compounding illusions
built partly on corrupted pre-1995 open prices, and the tradeable version
of the edge is small in dollars: **$1,000/day captures ≈ $390–490/year
gross** — commissions can consume all of it (IBKR fixed $1/side = −$115/yr
net), and even executed free it comes with single-name tail risk (worst
night −16%; the overnight leg lost 64% in 2022; max drawdown of the
compounded overnight curve ≈ −54%). The broader "overnight effect" this
chart generalizes from has **decayed to ~zero at index level since 2021**
(NY Fed 2026, "The Disappearing Overnight Drift") and the one live product
family that traded it (NightShares NSPY/NIWM, 2022–23) lost money and
liquidated in 13 months. What survives is a name-specific, retail-attention
phenomenon concentrated in semiconductors — genuine as an anomaly,
marginal as a retail strategy, and structurally at risk of regime flips
(AAPL and INTC both flipped sign across eras).

## 1. Replication on MU

Decomposition identity: `(1+overnight)·(1+intraday) = close/prev_close`,
with `overnight_t = open_t/close_{t-1} − 1`, `intraday_t = close_t/open_t − 1`.

| window | nights | overnight cum | intraday cum | ON bps/night | ID bps/night | ON t-stat |
|---|---|---|---|---|---|---|
| 1990–2017 (as-is, incl. dirty opens) | 7,019 | +10,405,264% | −100.0% | +18.0 | −5.6 | 8.6 |
| 1995–2017 (clean opens) | 5,754 | +2,623,268% | −99.99% | +19.3 | −9.8 | 8.1 |
| 2005–2017 (pristine) | 3,238 | +21,673% | −98.3% | +17.9 | −8.5 | 6.4 |
| 1995–2026-09 (spliced) | ~7,950 | +48,207,226% | −100.0% | +18.3 (CI 14.3–22.4) | −6.0 | 8.5 |
| **2018–2026-09** | 2,181 | **+1,616%** | **+47.7%** | **+15.4** | **+4.9** | **3.3** |
| 2024–2026-09 | 672 | +825% | +29.8% | +37.2 | +8.4 | 3.4 |

Start-year sensitivity (1990/93/95/2000/05/10/13/15): overnight mean is
16–20 bps/night in **every** window — the effect is not an artifact of any
one era. Median overnight return is positive (+12.9 bps); excluding the 20
best nights still leaves +423,305% (1995–2017); nights with |move|>5% are
2.4% of the sample and contribute only ~7% of the log total. It is a broad
drift, not just earnings gaps.

**But two caveats against the chart as printed:**

1. **Pre-1995 opens are largely fake in retail datasets.** In the same
   dataset the chart lineage uses, INTC 1972–82 has >85% of days with
   open==close (synthetic opens: the whole daily move is credited to
   overnight); INTC 1983–92 has >44% open==prev-close (stale opens); MU
   1989–94 has ~11% synthetic + ~30% stale. CRSP publishes **no** open
   prices at all for 1962–1992. Any cumulative product starting 1990
   compounds through years where the overnight/intraday split is
   unmeasurable. Our clean-window numbers above are the defensible core.
2. **Since 2018 the intraday leg is POSITIVE (+5 bps/night).** The
   "intraday loses everything" half of the chart is a pre-2018 story. The
   overnight leg still dominates ~3:1, but it no longer mirrors an
   intraday loss.

## 2. Expansion: is this a general law?

No. Clean-era decomposition, 34 names 2005–2017 and 26 names 2018–2026:

* **Overnight-dominated (2018–2026):** the semiconductor/hardware complex —
  MU +15.4, TSM +17.1, MRVL +18.5, AMD +18.0, AVGO +14.4, NVDA +16.2,
  WDC +11.8 bps/night — plus retail favorites (SOFI +19.2), F, TSLA.
  Several (TSM, MRVL, ORCL, UBER, SOFI) still pair it with *negative*
  intraday.
* **Intraday-dominated (same window):** AAPL (+0.6 ON vs +10.7 ID),
  GOOG (+1.2 vs +9.3), META, PLTR (+12.6 vs +16.7), JPM, JNJ, PG, XOM.
* **Regime flips are real:** AAPL was strongly overnight 2005–2017
  (+16.5 vs −2.5) and flipped completely after 2018. INTC flipped the
  other way. QCOM, MSFT, SBUX, TXN, ORCL all changed sign across eras.
* **Index level:** QQQ 1999–2026: overnight +3,421% vs intraday −53% —
  the classic result replicates. But post-2021 SPY is +3.2 ON vs +2.9 ID:
  the aggregate-market night premium is **gone**, exactly as the NY Fed's
  2026 follow-up reports (the 2–3 a.m. ET drift that averaged ~3.7%/yr
  pre-2021 is ~zero since).

In 2005–2017, only 17/34 names had overnight > intraday. The chart picks
one of the most extreme names in the market (Knuteson's own gallery uses
MU, HOLX, NVR, JBHT as showcase names).

## 3. Why it exists (literature)

* **Retail attention / opening-auction pressure** (Berkman et al. 2012;
  Lachance 2015/23; Elm Wealth 2024/25): retail buy orders queue overnight
  and hit an illiquid opening auction; opens print high and fade in the
  first hour. The effect concentrates in high-retail-attention names.
  The "overnight return" is partly a premium *paid by buyers at the open* —
  the strategy sells into exactly that.
* **Clientele tug-of-war** (Lou–Polk–Skouras 2019, JFE): anomaly returns
  split cleanly into overnight vs intraday components with opposite signs —
  the cross-sectional structure we see (semis vs AAPL/PG) is standard.
* **Dealer inventory risk** (Boyarchenko–Larsen–Whelan, RFS 2023): the
  index-level drift was compensation for absorbing closing imbalances; it
  shrank as imbalances shrank — hence the post-2021 index-level decay.
* **Overnight news flow** (Glasserman et al. 2025) explains part of the gap.
* **Manipulation hypothesis** (Knuteson himself): not accepted by the
  academic mainstream, not formally refuted; the retail-flow explanation
  is preferred because the effect clusters exactly where retail clusters.
* **CAPM day/night split** (Hendershott et al. 2020, JFE): beta is priced
  overnight, negatively intraday — high-beta names (MU!) mechanically tilt
  overnight. MU is a leveraged-beta, retail-heavy, news-heavy stock: it
  sits at the intersection of every known driver.

## 4. The $1,000/day bot, honestly costed

Gross expectancy: 252 × $1,000 × ~15–19 bps ≈ **$390–490/year**. That is
the whole prize (fixed $1k/day does not compound). The chart's +138M%
requires reinvesting an exponentially growing position — by the end you
would be buying MU's entire closing auction, ~$1bn+ nightly.

| cost scenario (per side) | net/yr on $1,000/day, 2018–2026 pace |
|---|---|
| free execution at auction prints | ≈ +$389 |
| 1 bp (optimistic all-in slippage) | ≈ +$339 |
| 2.5 bps | ≈ +$263 |
| IBKR Pro tiered, $0.35 min/side (≈3.5 bps) | ≈ +$215 |
| IBKR Pro fixed, $1.00 min/side (=10 bps) | ≈ **−$115** |

Execution notes (UK-resident retail):
* MOC by 15:55 ET, MOO by 09:28 ET (Nasdaq crosses) — fills at the official
  auction prints, which ARE the printed open/close in the data, so the
  gross numbers are approximately capturable at auction; **but** opening
  auctions are the most expensive venue for impact (Goyal–Jegadeesh–Wu
  2026) and the strategy must sell at the open every day.
* IBKR Lite ($0 commission) is not available to UK residents; IBKR Pro
  tiered ≈ $0.35 min/side. Alpaca serves UK users commission-free but
  without guaranteed true auction participation.
* $1,000 buys ~1 share of MU at $1,016 (fractional shares are generally
  not auction-eligible — order sizing is now lumpy).
* UK tax: this is CGT (Salt v Chamberlain; HMRC BIM56850), with same-day
  and 30-day matching rules; ~250 disposals/year, each computed in GBP at
  that day's FX. Above the £3,000 annual exempt amount, gains are taxed;
  losses are capital losses only.
* Risk actually carried: worst single night −15.6%; overnight leg summed
  to **−64% in 2022**; compounded overnight-only curve max drawdown −54%.
  You eat every earnings gap (MU reports after the close).

The strategy is *implementable* — it is just small, fragile, and
concentrated. Scaling it up keeps the bps but scales the tail risk and
the tax/operational drag, and history says the regime can flip (it did
for AAPL, INTC, and the whole index).

## 5. Live falsification: NightShares

NSPY / NIWM (launched June 2022, 0.55% ER) implemented exactly
buy-at-close-sell-at-open on index futures. In 13 months NSPY returned
≈ **−6.9%** while the S&P 500 rose ≈ +22%; both funds liquidated July 2023
with ~$5M combined AUM. The night effect inverted at the index level over
their whole life. Anyone proposing the single-name version should know the
index version died in production three years ago.

## 6. What we could NOT verify

* The chart's exact printed figures (+138,330,342% / −99.92%) — they are
  vintage- and start-date-sensitive; with MU's 10× split factor (5:2 1994,
  2:1 1995, 2:1 2000) and the 2026 melt-up the implied ~1,100× total
  return for 1990→2025/26 is in the plausible range, but the pre-1995
  segment rests on opens that are provably unreliable in retail data.
* Intraday microstructure (does MU's open still fade in the first hour?) —
  we have daily bars only; the literature (Berkman et al.) says yes
  historically.

## 7. If you still want the bot

A defensible version, in order of preference:

1. **Paper-trade it first** (Alpaca paper API or IBKR paper account):
   MOC+MOO pair on MU, $1k/day, log fills vs official auction prints for
   3 months. That measures YOUR real slippage — the single decisive input.
2. Trade it only if measured round-trip cost < ~5 bps and you accept
   −60%+ overnight-leg years as a base case.
3. Diversify the leg across the names where the effect is currently
   strongest (MU, TSM, MRVL, AMD, AVGO) rather than MU alone — same
   gross bps, less single-name gap risk.
4. Size it as entertainment, not income: at $1k/day the expected net is
   lunch money; at sizes where it matters, you are running a real
   short-vol-at-open book with tax paperwork to match.

The repo's Vigil platform deliberately contains **no brokerage
connectivity** (see README), so a live-trading bot is out of scope here;
the analysis code in this directory is the reusable deliverable.

## Reproduce

```bash
cd research/overnight_intraday
python3 study.py --data-dir <marjanovic>/data            # 1962-2017 universe
python3 study.py --data-dir <marjanovic>/data --start 2005-01-01 --out results/modern
python3 extend_study.py                                   # 2016-2026 + splices
python3 mu_deepdive.py --data-dir <marjanovic>/data       # MU robustness
```

Data provenance and cross-checks: see `data_sources.md`. All numbers in
this report were verified by an independent re-implementation and a
hostile code review (see `verification.md`).
