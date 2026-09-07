#!/usr/bin/env python3
"""MU deep-dive: robustness of the overnight edge and $1000/day bot economics.

Answers, for Micron specifically:
1. Start-year sensitivity - is the headline driven by early (dirty) data?
2. Tail concentration - is the edge a steady drift or a handful of
   earnings gaps you cannot afford to miss (or to catch on the wrong side)?
3. The $1000/day close->open strategy: per-year P&L before/after costs,
   drawdown profile of the overnight-only equity curve, worst days.

Usage: python3 mu_deepdive.py --data-dir <marjanovic-data-dir> [--ticker mu]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

from study import load_rows, find_file, leg_stats, TRADING_DAYS
from datetime import date


def returns_from(rows):
    on, intr, dates = [], [], []
    for prev, cur in zip(rows, rows[1:]):
        if (cur.d - prev.d).days > 10:
            continue
        on.append(cur.open / prev.close - 1)
        intr.append(cur.close / cur.open - 1)
        dates.append(cur.d)
    return on, intr, dates


def pct(x):
    return f"{x*100:,.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--ticker", default="mu")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "results"))
    args = ap.parse_args()

    path = find_file(args.data_dir, args.ticker)
    if not path:
        print(f"no data file for {args.ticker}")
        return 1
    rows, _ = load_rows(path)
    out = {"ticker": args.ticker}

    # 1. start-year sensitivity ---------------------------------------
    print(f"=== {args.ticker.upper()} start-year sensitivity (to {rows[-1].d}) ===")
    hdr = (f"{'start':<7}{'days':>6}{'ON cum':>16}{'ID cum':>12}"
           f"{'ON bps/d':>10}{'ID bps/d':>10}{'ON t':>7}{'ON win%':>8}")
    print(hdr)
    sens = []
    for y in (1990, 1993, 1995, 2000, 2005, 2010, 2013, 2015):
        sub = [r for r in rows if r.d.year >= y]
        if len(sub) < 300:
            continue
        on, intr, _ = returns_from(sub)
        so, si = leg_stats(on), leg_stats(intr)
        sens.append({"start": y, "n": so.n,
                     "on_cum_pct": so.cum_return_pct, "id_cum_pct": si.cum_return_pct,
                     "on_bps": so.mean_bps, "id_bps": si.mean_bps,
                     "on_t": so.t_stat, "on_win": so.win_rate})
        print(f"{y:<7}{so.n:>6}{so.cum_return_pct:>15,.0f}%{si.cum_return_pct:>11,.1f}%"
              f"{so.mean_bps:>10.2f}{si.mean_bps:>10.2f}{so.t_stat:>7.2f}"
              f"{so.win_rate*100:>8.1f}")
    out["start_year_sensitivity"] = sens

    # 2. tail concentration (clean era 1995+) --------------------------
    sub = [r for r in rows if r.d.year >= 1995]
    on, intr, dates = returns_from(sub)
    n = len(on)
    s_on = sorted(on)
    median_on = s_on[n // 2]
    mean_on = sum(on) / n
    total_log = sum(math.log1p(r) for r in on)
    top_idx = sorted(range(n), key=lambda i: on[i], reverse=True)
    log_top20 = sum(math.log1p(on[i]) for i in top_idx[:20])
    log_bot20 = sum(math.log1p(on[i]) for i in top_idx[-20:])
    gap_days = [i for i in range(n) if abs(on[i]) > 0.05]
    log_gaps = sum(math.log1p(on[i]) for i in gap_days)
    print(f"\n=== tail structure of {args.ticker.upper()} overnight leg, 1995+ ({n} nights) ===")
    print(f"mean {mean_on*1e4:.1f} bps  median {median_on*1e4:.1f} bps  "
          f"win rate {sum(1 for r in on if r > 0)/n:.1%}")
    print(f"cumulative log-return {total_log:.2f} "
          f"(= {pct(math.expm1(total_log))} compounded)")
    print(f"  top 20 nights contribute  {log_top20:.2f} ({log_top20/total_log:.0%} of log total)")
    print(f"  bottom 20 nights cost     {log_bot20:.2f}")
    print(f"  nights with |move|>5%: {len(gap_days)} "
          f"({len(gap_days)/n:.1%}), their log contribution {log_gaps:.2f} "
          f"({log_gaps/total_log:.0%} of log total)")
    excl = total_log - log_top20
    print(f"  excluding top 20 nights the compounded return is {pct(math.expm1(excl))}")
    out["tail_1995plus"] = {
        "n": n, "mean_bps": round(mean_on*1e4, 2), "median_bps": round(median_on*1e4, 2),
        "cum_log": round(total_log, 3), "top20_log": round(log_top20, 3),
        "bottom20_log": round(log_bot20, 3), "n_gap5": len(gap_days),
        "gap5_log": round(log_gaps, 3),
        "cum_excl_top20_pct": round(math.expm1(excl)*100, 1),
    }

    # 3. $1000/day bot economics (clean era 1995+) ----------------------
    print(f"\n=== $1000/day close->open bot on {args.ticker.upper()}, 1995+ ===")
    for cost_side_bps in (0.0, 1.0, 2.5, 5.0):
        pnl = [1000 * (r - 2 * cost_side_bps / 1e4) for r in on]
        total = sum(pnl)
        yearly = {}
        for i, d in enumerate(dates):
            yearly.setdefault(d.year, []).append(pnl[i])
        yr_sums = {y: sum(v) for y, v in yearly.items()}
        neg_years = sum(1 for v in yr_sums.values() if v < 0)
        worst_year = min(yr_sums, key=yr_sums.get)
        best_year = max(yr_sums, key=yr_sums.get)
        # max drawdown of cumulative P&L
        cum = peak = 0.0
        mdd = 0.0
        for p in pnl:
            cum += p
            peak = max(peak, cum)
            mdd = min(mdd, cum - peak)
        print(f"cost {cost_side_bps:>4.1f} bps/side: total P&L ${total:>10,.0f}  "
              f"avg/yr ${total/ (n/TRADING_DAYS):>7,.0f}  losing yrs {neg_years}/{len(yr_sums)}  "
              f"worst yr {worst_year} ${yr_sums[worst_year]:,.0f}  "
              f"best yr {best_year} ${yr_sums[best_year]:,.0f}  maxDD ${mdd:,.0f}")
        if cost_side_bps == 0.0:
            out["bot_zero_cost"] = {
                "total": round(total), "yearly": {str(y): round(v) for y, v in yr_sums.items()},
                "max_dd": round(mdd),
            }
        if cost_side_bps == 2.5:
            out["bot_2p5bps"] = {"total": round(total), "losing_years": neg_years,
                                 "n_years": len(yr_sums), "max_dd": round(mdd)}
    worst_nights = sorted(range(n), key=lambda i: on[i])[:5]
    print("worst nights:", [(dates[i].isoformat(), f"{on[i]*100:.1f}%") for i in worst_nights])
    out["worst_nights"] = [(dates[i].isoformat(), round(on[i]*100, 1)) for i in worst_nights]

    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, f"{args.ticker}_deepdive.json")
    with open(out_path, "w") as fh:
        json.dump(out, fh, indent=1)
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
