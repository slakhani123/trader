#!/usr/bin/env python3
"""Recent-era (2016 -> 2026) overnight/intraday study + long spliced series.

The Marjanovic dataset ends 2017-11-10. This module extends the analysis
through 2026-09 using three independently-collected committed GitHub
datasets (all yfinance-derived but fetched by different parties at
different times, which gives some protection against a single bad pull):

  A. yennanliu/finance_data      data/prices/<t>.csv
     date,open,high,low,close,volume,div,split
     split-adjusted, dividends NOT price-adjusted (div = cash on ex-date)
     -> overnight leg adds the ex-date dividend: (O_t + div_t)/C_{t-1} - 1
  B. KartikPahadiya/stock-market-prediction  data/raw/stocks/<T>.csv
     Date,Close,High,Low,Open,Volume   (auto_adjust=True total-return-ish)
  C. asrith-306/market-analytics-toolkit     datasets/<T>.csv
     Date,Open,High,Low,Close,Volume   (auto_adjust=True)

Outputs
  * per-ticker decomposition for windows: full, 2018+, 2021+, 2024+
  * spliced long series (Marjanovic returns + recent returns) for MU,
    QQQ, SPY with overlap validation (daily close-to-close return diffs
    on common dates - adjustment-base independent)
  * results/recent_summary.json

Stdlib only.
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
from datetime import date

from study import DayRow, leg_stats, bootstrap_mean_ci, TRADING_DAYS, find_file, load_rows

HERE = os.path.dirname(os.path.abspath(__file__))
FINANCE_DATA = "/home/user/yennanliu/finance_data/data/prices"
KARTIK = "/home/user/kartikpahadiya/stock-market-prediction/data/raw/stocks"
ASRITH = "/home/user/asrith-306/market-analytics-toolkit/datasets"
MARJANOVIC = "/home/user/scienclick/stocks/data"

# ticker -> (source, path). Dividend-aware source A preferred when present.
RECENT_SOURCES: dict[str, tuple[str, str]] = {}
for t in ("mu", "amd", "amzn", "goog", "intc", "meta", "msft", "nvda", "qqq",
          "tsla", "vti", "orcl", "tsm", "avgo", "wdc", "pltr", "sofi", "uber",
          "mrvl"):
    RECENT_SOURCES[t] = ("A", os.path.join(FINANCE_DATA, f"{t}.csv"))
for t in ("aapl", "googl", "jnj", "jpm", "pg", "xom"):
    RECENT_SOURCES[t] = ("B", os.path.join(KARTIK, f"{t.upper()}.csv"))
RECENT_SOURCES["spy"] = ("C", os.path.join(ASRITH, "SPY.csv"))


def load_recent(src: str, path: str):
    """-> (rows: list[DayRow], div_by_date: dict[date, float])"""
    rows: list[DayRow] = []
    divs: dict[date, float] = {}
    with open(path, newline="") as fh:
        for rec in csv.DictReader(fh):
            key_date = "date" if "date" in rec else "Date"
            try:
                d = date.fromisoformat(rec[key_date][:10])
                o = float(rec["open" if src == "A" else "Open"])
                c = float(rec["close" if src == "A" else "Close"])
            except (KeyError, ValueError):
                continue
            if o <= 0 or c <= 0:
                continue
            rows.append(DayRow(d, o, c))
            if src == "A":
                dv = (rec.get("div") or "").strip()
                if dv:
                    try:
                        v = float(dv)
                        if v > 0:
                            divs[d] = v
                    except ValueError:
                        pass
    rows.sort(key=lambda r: r.d)
    return rows, divs


def daily_legs(rows, divs=None):
    """-> (dates, overnight, intraday) with ex-date dividends credited to
    the overnight leg when the source does not price-adjust them."""
    divs = divs or {}
    dates, on, intr = [], [], []
    for prev, cur in zip(rows, rows[1:]):
        if (cur.d - prev.d).days > 10:
            continue
        dv = divs.get(cur.d, 0.0)
        if dv / cur.close > 0.05:      # guard: mis-scaled dividend row
            dv = 0.0
        on.append((cur.open + dv) / prev.close - 1)
        intr.append(cur.close / cur.open - 1)
        dates.append(cur.d)
    return dates, on, intr


def window_stats(dates, on, intr, start_year: int | None):
    if start_year:
        keep = [i for i, d in enumerate(dates) if d.year >= start_year]
    else:
        keep = list(range(len(dates)))
    if len(keep) < 60:
        return None
    o = [on[i] for i in keep]
    i_ = [intr[i] for i in keep]
    so, si = leg_stats(o), leg_stats(i_)
    return {
        "n": so.n,
        "from": dates[keep[0]].isoformat(),
        "to": dates[keep[-1]].isoformat(),
        "on_cum_pct": so.cum_return_pct, "id_cum_pct": si.cum_return_pct,
        "on_bps": so.mean_bps, "id_bps": si.mean_bps,
        "on_t": so.t_stat, "id_t": si.t_stat,
        "on_sharpe": so.sharpe, "on_win": so.win_rate,
    }


def splice_validation(rows_a, rows_b):
    """Compare daily close-to-close returns of two sources on common dates."""
    ra = {cur.d: cur.close / prev.close - 1 for prev, cur in zip(rows_a, rows_a[1:])}
    rb = {cur.d: cur.close / prev.close - 1 for prev, cur in zip(rows_b, rows_b[1:])}
    common = sorted(set(ra) & set(rb))
    if not common:
        return {"common_days": 0}
    diffs = [abs(ra[d] - rb[d]) * 1e4 for d in common]
    diffs.sort()
    return {
        "common_days": len(common),
        "median_abs_diff_bps": round(diffs[len(diffs) // 2], 2),
        "p95_abs_diff_bps": round(diffs[int(0.95 * len(diffs))], 2),
        "max_abs_diff_bps": round(diffs[-1], 2),
    }


def spliced_series(ticker: str, marj_start_year: int):
    """Concatenate Marjanovic returns (to the recent source's first date)
    with recent-source returns. Returns dict with series + stats or None."""
    src, path = RECENT_SOURCES[ticker]
    if not os.path.exists(path):
        return None
    marj_path = find_file(MARJANOVIC, ticker)
    if not marj_path:
        return None
    marj_rows, _ = load_rows(marj_path)
    marj_rows = [r for r in marj_rows if r.d.year >= marj_start_year]
    rec_rows, divs = load_recent(src, path)
    val = splice_validation(marj_rows, rec_rows)

    cut = rec_rows[0].d
    marj_part = [r for r in marj_rows if r.d < cut]
    d1, on1, id1 = daily_legs(marj_part)
    d2, on2, id2 = daily_legs(rec_rows, divs)
    dates = d1 + d2
    on = on1 + on2
    intr = id1 + id2

    so, si = leg_stats(on), leg_stats(intr)
    # yearly cumulative log10 curves
    years, s_on, s_id = [], [], []
    cum_on = cum_id = 0.0
    seen = None
    for i, d in enumerate(dates):
        cum_on += math.log1p(on[i])
        cum_id += math.log1p(intr[i])
        if seen != d.year:
            seen = d.year
            years.append(d.year)
            s_on.append(round(cum_on / math.log(10), 4))
            s_id.append(round(cum_id / math.log(10), 4))
    years.append(dates[-1].year + 1)
    s_on.append(round(cum_on / math.log(10), 4))
    s_id.append(round(cum_id / math.log(10), 4))

    return {
        "ticker": ticker,
        "from": dates[0].isoformat(), "to": dates[-1].isoformat(),
        "n": len(on),
        "splice_at": cut.isoformat(),
        "overlap_validation": val,
        "on_cum_pct": so.cum_return_pct, "id_cum_pct": si.cum_return_pct,
        "on_bps": so.mean_bps, "id_bps": si.mean_bps,
        "on_t": so.t_stat, "id_t": si.t_stat,
        "on_ci_bps": bootstrap_mean_ci(on),
        "series_years": years, "series_on_log10": s_on, "series_id_log10": s_id,
        "windows": {
            "2018+": window_stats(dates, on, intr, 2018),
            "2021+": window_stats(dates, on, intr, 2021),
            "2024+": window_stats(dates, on, intr, 2024),
        },
    }


def main() -> int:
    out = {"recent": [], "spliced": {}}

    print(f"{'ticker':<7}{'src':<4}{'from':<12}{'to':<12}"
          f"{'window':<7}{'n':>6}{'ON cum%':>12}{'ID cum%':>11}"
          f"{'ON bps':>8}{'ID bps':>8}{'ON t':>7}")
    for t, (src, path) in sorted(RECENT_SOURCES.items()):
        if not os.path.exists(path):
            print(f"{t:<7}{src:<4}MISSING {path}")
            continue
        rows, divs = load_recent(src, path)
        if len(rows) < 200:
            continue
        dates, on, intr = daily_legs(rows, divs)
        entry = {"ticker": t, "source": src,
                 "from": dates[0].isoformat(), "to": dates[-1].isoformat(),
                 "windows": {}}
        for label, sy in (("full", None), ("2018+", 2018), ("2021+", 2021), ("2024+", 2024)):
            w = window_stats(dates, on, intr, sy)
            if not w:
                continue
            entry["windows"][label] = w
            print(f"{t:<7}{src:<4}{w['from']:<12}{w['to']:<12}{label:<7}{w['n']:>6}"
                  f"{w['on_cum_pct']:>12,.1f}{w['id_cum_pct']:>11,.1f}"
                  f"{w['on_bps']:>8.2f}{w['id_bps']:>8.2f}{w['on_t']:>7.2f}")
        out["recent"].append(entry)

    print("\n=== spliced long series (Marjanovic + recent) ===")
    for t, sy in (("mu", 1995), ("qqq", 1999), ("spy", 2005)):
        s = spliced_series(t, sy)
        if not s:
            print(f"{t}: splice unavailable")
            continue
        out["spliced"][t] = s
        v = s["overlap_validation"]
        print(f"{t}: {s['from']} -> {s['to']}  ON {s['on_cum_pct']:,.0f}% "
              f"({s['on_bps']:.2f} bps/d, t={s['on_t']:.1f}, CI {s['on_ci_bps']}) "
              f"ID {s['id_cum_pct']:,.1f}% ({s['id_bps']:.2f} bps/d)")
        print(f"   splice at {s['splice_at']}; overlap {v.get('common_days',0)} days, "
              f"median diff {v.get('median_abs_diff_bps','-')} bps, "
              f"p95 {v.get('p95_abs_diff_bps','-')} bps, max {v.get('max_abs_diff_bps','-')} bps")
        for wl, w in s["windows"].items():
            if w:
                print(f"   {wl}: ON {w['on_bps']:+.2f} bps/d (t={w['on_t']:.1f}) "
                      f"ID {w['id_bps']:+.2f} bps/d  ON cum {w['on_cum_pct']:,.1f}% "
                      f"ID cum {w['id_cum_pct']:,.1f}%")

    out_path = os.path.join(HERE, "results", "recent_summary.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(out, fh, indent=1)
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
