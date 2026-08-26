# Backtesting and evaluation

The backtester (`backend/src/vigil/backtest/`) replays history through the
SAME snapshot builder, engines, composite scorer and signal rules used
live — there is no separate formula copy to drift out of sync.

## Bias controls

| Bias | Control |
|------|---------|
| Look-ahead | every input flows through `data/snapshot.build_snapshot(as_of)`; fundamentals gated on `published_at`, estimates on snapshot date, corporate actions on `ex_date`, macro on release date |
| Survivorship | delisted instruments (e.g. the acquired TLLM in the demo world) stay in the historical universe and are tradeable until delisting; delisting proceeds use the final bar |
| Restatement | restated figures only replace originals from the restatement's own publication date |
| Stale prices | staleness gates block signals on stale quotes; staleness is measured per snapshot |
| Index composition | the demo world has a stable universe; for real data, store dated universe membership and filter the scan loop by membership on each date (hook: `universe_on(date)`) — documented limitation until a constituents source is configured |
| Costs | commission (5bp/side default) + half-spread by liquidity band + 1-day execution delay (signal at close, fill at next open) |
| Repeated optimisation | designate a holdout period (`--holdout-start`); report in-sample and holdout separately and do NOT iterate weights against the holdout |

## Protocol

- Walk-forward: scan dates step through history (default every 5 trading
  days) using only then-visible data; signals enter at the next session's
  open and follow the SAME lifecycle rules (trim/exit/invalidation/stops)
  to their close. The full buy→trim/exit lifecycle is what gets measured,
  not just entries.
- Time-series-aware splits: calibration must use walk-forward or expanding
  windows, never shuffled K-fold.
- Benchmarks: each trade is compared against its market benchmark over the
  same holding window (alpha per trade); portfolio-level metrics compare
  against the blended benchmark.

## Reported metrics

Per strategy family, horizon, sector, market regime, market-cap band and
score bucket: n (with Wilson 95% CI on hit rate), total return, alpha vs
matched benchmark, hit rate, precision/recall vs a "beat benchmark by
>2%" outcome definition, average/median return, volatility, Sharpe,
Sortino, max drawdown, turnover, average holding period, MAE, MFE, alert
frequency. Calibration: reliability curves (predicted-success bins from
opportunity×confidence mapping vs observed frequency) + Brier score.
Sample sizes and intervals are displayed everywhere — a bucket with n<20
is labelled inconclusive.

## Shadow / paper mode

Live alerts ARE the shadow record: `alerts` rows are immutable, carry the
full evidence packet, scores, price and timestamps at issue time, and are
never rewritten with later information. Outcome evaluation reads alerts
and subsequent prices only. Historical signals are never regenerated with
newer data; a new model version starts a new track record.

## Calibration loop

1. Run the backtest excluding the holdout.
2. Inspect per-bucket hit rates and reliability curves.
3. Adjust weights → register a NEW model version (`scoring/weights.py`),
   never edit v1.0.0 in place.
4. Confirm on the untouched holdout once; further iteration needs a new
   holdout boundary.
5. The `UNCALIBRATED_PENALTY` on confidence may be reduced only after a
   version shows acceptable holdout calibration (documented in the model
   version notes).
