# Verification record

Three independent adversarial checks were run against this study before
publication (2026-09-07). Summary: **no critical or major issues; all five
headline conclusions survived; all numeric claims reproduced.**

## 1. Clean-room replication

An independent re-implementation (written without reading the study code,
stdlib only, from the same raw files) reproduced:

| quantity | study | replication |
|---|---|---|
| MU 1995–2017 overnight cum | +2,623,268% | +2,608,998% ¹ |
| MU 1995–2017 overnight mean | 19.33 bps | 19.32 bps |
| MU 1995–2017 intraday mean | −9.82 bps | −9.82 bps |
| MU 2018–2026 overnight mean / t | 15.43 bps / 3.28 | 15.4338 bps / 3.2827 |
| $1000/day gross P&L per year | ≈ $389 | $388.93 |
| MU close 2026-09-04 parsed | 1016.59 | 1016.59 |

¹ The 0.5% difference is a window convention: the replication includes the
1994-12-30→1995-01-03 boundary night; the study starts pairs inside 1995.
Both are valid; all other digits agree.

## 2. Hostile code review

Verified correct by tracing code and executing probes against real data:
return pairing; the identity (1+on)(1+id)=C_t/C_{t−1} (holds to 2.2e-16
over all 7,178 MU pairs); log1p compounding, annualisation, Sharpe and
t-stat; the circular block bootstrap (reproduces analytic CIs on iid
data); dividend handling in all three source regimes (source A raw +
cash div column, B/C auto-adjusted, Marjanovic adjusted — empirically
confirmed, no double-count and no miss; AVGO's div column verified
split-consistent); splice concatenation (no overlap, no double count);
the cost model and drawdown arithmetic.

Findings (all minor/cosmetic; dispositions):

1. **Splice drops the cut day's returns entirely** (both legs, one day per
   spliced series; ~0.6% multiplicative on a 48M% figure). *Disposition:
   documented in code and here; immaterial, not re-run.*
2. **Overlap-validation p95/max stats include ex-dividend gaps** for
   dividend-unadjusted source A (QQQ's 29.5 bps "max diff" is exactly its
   2016-12-16 distribution). The spliced series themselves are correct.
   *Disposition: docstring corrected.*
3. **Residual synthetic-open contamination in kept early-1980s years**
   (old-economy names) shifts overnight means by ≤ ~1.2 bps — enough to
   flip only MCD's near-zero sign; could move the "19/34 names" count by
   one. Does not touch MU, semis, or any 1993+ number. *Disposition:
   disclosed; headline claims unaffected.*
4. **Dividend guard denominator** used same-day close (prev close is
   economically right); never fires on current data (max observed ratio
   3.7%). *Disposition: fixed.*
5. **Latent hazards that verifiably never fire on current data**: dropped
   non-positive rows could mis-pair around a 1-day hole (zero rows dropped
   in practice); a >10-day gap discards the resumed day's intraday return
   (zero gaps in practice); Feb-29-ending datasets crashed the last-5y cut
   (*fixed*).
6. **mu_deepdive's "1995+" cut is hand-picked** — stricter than the trim
   rules require for MU; the start-year sensitivity table shows it is
   immaterial (overnight mean 16–20 bps from every start year).
7. **t-stats assume iid** on heavy-tailed returns and overstate precision;
   the block-bootstrap CI (14.3–22.4 bps for MU 1995–2026) is the primary
   evidence line. *Disposition: both quoted in the report.*
8. Chart-series yearly sampling off-by-one-day; sub-pixel on a log chart.
   *Disposition: accepted.*

## 3. Adversarial refutation of the five conclusions

Independent agent instructed to refute; none refuted.

1. *MU pattern real on clean data* — *not refuted (high confidence)*.
   Reproduced from raw data; bootstrap CI excludes zero; pattern persists
   in the pristine 2005+ era; corroborated externally (Knuteson's own
   published MU figures, Lachance 2023, Cooper–Cliff–Gulen).
2. *Edge persisted 2018–2026* — *not refuted (medium)*. Caveats adopted
   into the report: the three recent sources share yfinance lineage
   upstream; intraday "positive" is statistically flat (t≈0.9); MU is a
   post-hoc selection (mitigated by TSM/AMD/AVGO/NVDA showing the same
   pattern at t≈3.8–4.8).
3. *Bot economics* — *not refuted (medium)*. All tail/cost figures
   reproduced. Corrections adopted: Alpaca's auction orders (OPG/CLS) are
   gated behind Alpaca Elite (~$30k minimum); IBKR UK = Pro pricing only;
   Robinhood UK/Trading 212 lack on-open/on-close order types. Also
   noted: FINRA's PDT rule was abolished June 2026 and never applied to
   overnight round-trips anyway; T+1 settlement permits the daily cycle
   in a cash account.
4. *Semis-concentrated, regime-flipping, index version dead* — *not
   refuted (medium)*, with one correction adopted: JPM (and XOM) are
   overnight-dominant over the full 2018+ window, tipping intraday only
   from 2021+; PLTR is borderline. AAPL's flip confirmed genuine.
5. *Chart internal consistency* — resolved with data: both price lineages
   agree MU's split-adjusted close was ≈$1.00 in Jan 1990, so the chart's
   implied ~1,106× total matches MU's actual ~960–1,015× as of the
   late-Aug-2026 viral date. The chart is internally consistent for a
   1990→Aug-2026 window; the study's earlier "inconsistent" hypothesis
   was withdrawn and the report corrected.

## Residual risks that verification cannot remove

* All recent-era sources are yfinance-lineage; a common-mode upstream
  error would evade the cross-checks (mitigated by the contemporaneous
  pystock-data agreement on 2016–17 and the live-quote match on the last
  bar, both of which passed).
* Daily bars cannot show whether MU's open still fades in the first hour
  (the Berkman et al. mechanism); execution-price fidelity beyond the
  auction-print argument is untested without live fills.
* Persistence risk is inherent: the same decomposition showed AAPL
  flipping regimes and the index-level effect dying post-2021. No amount
  of backtest verification converts a historical tilt into a forward
  guarantee.
