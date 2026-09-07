#!/usr/bin/env python3
"""Overnight vs intraday return decomposition study.

Tests the claim (viral LinkedIn chart, Sep 2026) that almost all of Micron
Technology's (MU) long-run gain came from overnight moves (close -> next
open) while the intraday leg (open -> close) lost money, and expands the
same decomposition across a wider universe of liquid US stocks and ETFs.

Method
------
For each trading day t with previous trading day t-1:

    overnight_t = Open_t  / Close_{t-1} - 1     (close -> next open)
    intraday_t  = Close_t / Open_t      - 1     (open -> close)
    total_t     = Close_t / Close_{t-1} - 1

so that (1 + overnight_t) * (1 + intraday_t) == 1 + total_t exactly.

Cumulative legs are the compounded products of each daily series. This is
exactly the "buy at close, sell at next open" strategy (overnight leg,
before costs) vs "buy at open, sell at close" (intraday leg).

Data quality guards
-------------------
* rows with non-positive open/close are dropped;
* days whose open == close exactly are counted per year (a high share
  signals synthetic opens, which would silently zero the intraday leg);
* gaps > 10 calendar days are counted (halts / listing gaps);
* the largest absolute overnight moves are surfaced for manual review
  (a missed split adjustment shows up as a spurious ~-50% overnight);
* the identity (1+on)(1+id) = C_t/C_{t-1} holds by construction, so
  cross-source verification is done against an independent dataset in
  verify_cross_source.py, not here.

Stdlib only: the research container has no access to third-party wheels.

Usage
-----
    python3 study.py --data-dir /path/to/marjanovic/data \
        [--tickers mu,aapl,...] [--out results/]

Data files: Marjanovic "Huge Stock Market Dataset" layout, i.e.
<data-dir>/Stocks/<ticker>.us.txt and <data-dir>/ETFs/<ticker>.us.txt with
header Date,Open,High,Low,Close,Volume,OpenInt (split- and dividend-
adjusted OHLC; dividends therefore accrue to the overnight leg, which is
economically correct for a holder of the shares overnight).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sys
from dataclasses import dataclass, field, asdict
from datetime import date

TRADING_DAYS = 252

# Liquid, well-known names for the expansion universe. All are present in
# the Marjanovic dataset unless noted at runtime. Mix of mega-cap tech
# (where the claim should be strongest), old-economy blue chips, and index
# ETFs (the aggregate-market check).
DEFAULT_UNIVERSE = [
    # subject of the claim
    "mu",
    # mega-cap tech / semis
    "aapl", "msft", "nvda", "amzn", "googl", "tsla", "amd", "intc",
    "csco", "qcom", "txn", "orcl", "ibm", "nflx", "fb",
    # old-economy blue chips
    "ge", "jpm", "xom", "wmt", "ko", "jnj", "pg", "dis", "ba", "cat",
    "mcd", "nke", "sbux", "f",
    # index ETFs
    "spy", "qqq", "iwm", "dia",
]


@dataclass
class DayRow:
    d: date
    open: float
    close: float


@dataclass
class LegStats:
    n: int = 0
    mean_bps: float = 0.0          # arithmetic mean daily return, basis points
    std_bps: float = 0.0
    t_stat: float = 0.0
    win_rate: float = 0.0
    cum_return_pct: float = 0.0    # compounded total, percent
    ann_return_pct: float = 0.0    # geometric annualised, percent
    sharpe: float = 0.0            # mean/std * sqrt(252), rf=0


@dataclass
class TickerResult:
    ticker: str
    start: str
    end: str
    n_days: int
    overnight: LegStats = field(default_factory=LegStats)
    intraday: LegStats = field(default_factory=LegStats)
    total: LegStats = field(default_factory=LegStats)
    # data quality
    dropped_rows: int = 0
    open_eq_close_share: float = 0.0     # fraction of days open == close
    worst_year_open_eq_close: str = ""   # "YYYY:share"
    trimmed_from: str = ""               # first date kept after open-quality trim
    trim_reason: str = ""                # per-year artifact shares that forced the trim
    raw_start: str = ""                  # untrimmed first date, for reference
    n_large_gaps: int = 0
    top_overnight_moves: list = field(default_factory=list)  # [(date, pct)]
    # per-decade mean daily overnight/intraday in bps
    by_period: dict = field(default_factory=dict)
    # bootstrap 95% CI for mean overnight daily return (bps)
    on_mean_ci_bps: list = field(default_factory=list)
    # tradability: cost that zeroes the overnight edge, and expected annual
    # P&L of the $1000/day close->open strategy under per-SIDE cost scenarios
    breakeven_round_trip_bps: float = 0.0
    ann_pnl_per_1000_by_cost: dict = field(default_factory=dict)
    # yearly sampled cumulative log10 series for charting
    series_years: list = field(default_factory=list)
    series_on_log10: list = field(default_factory=list)
    series_id_log10: list = field(default_factory=list)


def load_rows(path: str) -> tuple[list[DayRow], int]:
    rows: list[DayRow] = []
    dropped = 0
    with open(path, newline="") as fh:
        for rec in csv.DictReader(fh):
            try:
                o = float(rec["Open"])
                c = float(rec["Close"])
                d = date.fromisoformat(rec["Date"])
            except (KeyError, ValueError):
                dropped += 1
                continue
            if o <= 0 or c <= 0:
                dropped += 1
                continue
            rows.append(DayRow(d, o, c))
    rows.sort(key=lambda r: r.d)
    return rows, dropped


def leg_stats(returns: list[float]) -> LegStats:
    n = len(returns)
    if n < 2:
        return LegStats(n=n)
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(var)
    log_sum = sum(math.log1p(r) for r in returns)
    cum = math.expm1(log_sum)
    years = n / TRADING_DAYS
    ann = math.expm1(log_sum / years) if years > 0 else 0.0
    return LegStats(
        n=n,
        mean_bps=round(mean * 1e4, 3),
        std_bps=round(std * 1e4, 1),
        t_stat=round(mean / (std / math.sqrt(n)), 2) if std > 0 else 0.0,
        win_rate=round(sum(1 for r in returns if r > 0) / n, 4),
        cum_return_pct=round(cum * 100, 1),
        ann_return_pct=round(ann * 100, 2),
        sharpe=round(mean / std * math.sqrt(TRADING_DAYS), 2) if std > 0 else 0.0,
    )


def bootstrap_mean_ci(returns: list[float], n_boot: int = 2000,
                      block: int = 5, seed: int = 42) -> list[float]:
    """Circular block bootstrap 95% CI for the mean, in bps/day."""
    n = len(returns)
    if n < 100:
        return []
    rng = random.Random(seed)
    n_blocks = math.ceil(n / block)
    means = []
    for _ in range(n_boot):
        acc = 0.0
        cnt = 0
        for _ in range(n_blocks):
            s = rng.randrange(n)
            for k in range(block):
                if cnt >= n:
                    break
                acc += returns[(s + k) % n]
                cnt += 1
        means.append(acc / cnt)
    means.sort()
    lo = means[int(0.025 * n_boot)] * 1e4
    hi = means[int(0.975 * n_boot)] * 1e4
    return [round(lo, 2), round(hi, 2)]


def open_quality_trim(rows: list[DayRow]) -> tuple[list[DayRow], str, str]:
    """Drop early years whose opens look synthetic.

    Two artifacts poison the decomposition, in opposite directions:
      * open == close  (open copied from close): intraday leg forced to 0,
        the whole daily move lands on the overnight leg;
      * open == previous close (stale open): overnight leg forced to 0,
        the whole move lands on the intraday leg.
    Vendors backfilled 1960s-80s daily bars this way (e.g. INTC 1972-82:
    >85% open==close; INTC 1983-92: >44% open==prev-close). We use HARD
    thresholds (open==close > 25% of days, or open==prev-close > 40%)
    because pre-decimalization tick sizes (1/8, 1/16 until 2001) produce
    exact coincidences on 10-20% of days for perfectly genuine data.
    Years with >= 30 obs breaching a hard threshold are synthetic; we keep
    data from the year AFTER the last such year. Residual softer staleness
    (opc 10-20% through the mid-90s fraction era) is reported, not
    trimmed - it biases the overnight leg DOWN (stale opens move overnight
    gains into the intraday leg), i.e. against the hypothesis under test.
    """
    OEQ_MAX = 0.25
    OPC_MAX = 0.40
    by_year: dict[int, list[int]] = {}   # year -> [n, n_oeq, n_opc]
    for i, r in enumerate(rows):
        c = by_year.setdefault(r.d.year, [0, 0, 0])
        c[0] += 1
        if r.open == r.close:
            c[1] += 1
        if i > 0 and r.open == rows[i - 1].close:
            c[2] += 1
    last_bad = None
    reasons = []
    for y in sorted(by_year):
        n, oeq, opc = by_year[y]
        if n >= 30 and (oeq / n > OEQ_MAX or opc / n > OPC_MAX):
            last_bad = y
            reasons.append(f"{y}:oeq={oeq/n:.0%},opc={opc/n:.0%}")
    if last_bad is None:
        return rows, "", ""
    kept = [r for r in rows if r.d.year > last_bad]
    reason = f"dropped <= {last_bad} ({len(rows) - len(kept)} rows; " \
             f"bad years e.g. {', '.join(reasons[-3:])})"
    return kept, kept[0].d.isoformat() if kept else "", reason


def analyse(ticker: str, rows: list[DayRow], dropped: int) -> TickerResult:
    raw_start = rows[0].d.isoformat() if rows else ""
    rows, trimmed_from, trim_reason = open_quality_trim(rows)

    on: list[float] = []          # overnight returns
    intr: list[float] = []        # intraday returns
    tot: list[float] = []
    dates: list[date] = []
    n_large_gaps = 0

    for prev, cur in zip(rows, rows[1:]):
        gap = (cur.d - prev.d).days
        if gap > 10:
            n_large_gaps += 1
            continue  # do not bridge halts/listing gaps with one "overnight"
        on.append(cur.open / prev.close - 1)
        intr.append(cur.close / cur.open - 1)
        tot.append(cur.close / prev.close - 1)
        dates.append(cur.d)

    res = TickerResult(
        ticker=ticker,
        start=rows[0].d.isoformat() if rows else "",
        end=rows[-1].d.isoformat() if rows else "",
        n_days=len(on),
        dropped_rows=dropped,
        n_large_gaps=n_large_gaps,
        raw_start=raw_start,
        trimmed_from=trimmed_from,
        trim_reason=trim_reason,
    )
    if not on:
        return res

    res.overnight = leg_stats(on)
    res.intraday = leg_stats(intr)
    res.total = leg_stats(tot)
    res.on_mean_ci_bps = bootstrap_mean_ci(on)

    # $1000/day buy-close-sell-open: expected annual P&L after costs.
    # cost_side is per side in bps (spread crossing + fees + slippage);
    # a round trip pays it twice. Arithmetic expectancy: fixed $1000/day.
    mean_on = res.overnight.mean_bps
    res.breakeven_round_trip_bps = round(mean_on, 2)
    for cost_side in (0.0, 1.0, 2.5, 5.0, 10.0):
        net_bps = mean_on - 2 * cost_side
        res.ann_pnl_per_1000_by_cost[f"{cost_side}bps_side"] = round(
            TRADING_DAYS * net_bps / 1e4 * 1000, 0
        )

    # open == close share, overall and worst year
    eq_by_year: dict[int, list[int]] = {}
    n_eq = 0
    for r in rows:
        y = r.d.year
        eq = 1 if r.open == r.close else 0
        n_eq += eq
        cnt = eq_by_year.setdefault(y, [0, 0])
        cnt[0] += eq
        cnt[1] += 1
    res.open_eq_close_share = round(n_eq / len(rows), 4)
    worst = max(
        ((y, c[0] / c[1]) for y, c in eq_by_year.items() if c[1] >= 30),
        key=lambda p: p[1],
        default=None,
    )
    if worst:
        res.worst_year_open_eq_close = f"{worst[0]}:{worst[1]:.2f}"

    # largest absolute overnight moves for manual inspection
    idx = sorted(range(len(on)), key=lambda i: abs(on[i]), reverse=True)[:5]
    res.top_overnight_moves = [
        (dates[i].isoformat(), round(on[i] * 100, 1)) for i in idx
    ]

    # per-period decomposition (by decade and the trailing 5 years of data)
    def period_key(d: date) -> str:
        return f"{d.year // 10 * 10}s"

    periods: dict[str, tuple[list[float], list[float]]] = {}
    for i, d in enumerate(dates):
        p = periods.setdefault(period_key(d), ([], []))
        p[0].append(on[i])
        p[1].append(intr[i])
    try:
        last5_cut = dates[-1].replace(year=dates[-1].year - 5)
    except ValueError:            # Feb 29 with no leap-year counterpart
        last5_cut = dates[-1].replace(year=dates[-1].year - 5, day=28)
    p = periods.setdefault("last5y", ([], []))
    for i, d in enumerate(dates):
        if d >= last5_cut:
            p[0].append(on[i])
            p[1].append(intr[i])
    for name, (o_l, i_l) in sorted(periods.items()):
        if len(o_l) < 30:
            continue
        res.by_period[name] = {
            "n": len(o_l),
            "on_mean_bps": round(sum(o_l) / len(o_l) * 1e4, 2),
            "id_mean_bps": round(sum(i_l) / len(i_l) * 1e4, 2),
            "on_cum_pct": round(math.expm1(sum(math.log1p(x) for x in o_l)) * 100, 1),
            "id_cum_pct": round(math.expm1(sum(math.log1p(x) for x in i_l)) * 100, 1),
        }

    # yearly-sampled cumulative log10 curves (for charts)
    cum_on = 0.0
    cum_id = 0.0
    seen_year = None
    for i, d in enumerate(dates):
        cum_on += math.log1p(on[i])
        cum_id += math.log1p(intr[i])
        if seen_year != d.year:
            seen_year = d.year
            res.series_years.append(d.year)
            res.series_on_log10.append(round(cum_on / math.log(10), 4))
            res.series_id_log10.append(round(cum_id / math.log(10), 4))
    # final point
    res.series_years.append(dates[-1].year + 1)
    res.series_on_log10.append(round(cum_on / math.log(10), 4))
    res.series_id_log10.append(round(cum_id / math.log(10), 4))
    return res


def find_file(data_dir: str, ticker: str) -> str | None:
    for sub in ("Stocks", "ETFs"):
        p = os.path.join(data_dir, sub, f"{ticker}.us.txt")
        if os.path.exists(p):
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--tickers", default=",".join(DEFAULT_UNIVERSE))
    ap.add_argument("--start", default="", help="clip start date YYYY-MM-DD")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "results"))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    clip = date.fromisoformat(args.start) if args.start else None

    results: list[TickerResult] = []
    missing: list[str] = []
    for t in [x.strip().lower() for x in args.tickers.split(",") if x.strip()]:
        path = find_file(args.data_dir, t)
        if not path:
            missing.append(t)
            continue
        rows, dropped = load_rows(path)
        if clip:
            rows = [r for r in rows if r.d >= clip]
        if len(rows) < 100:
            missing.append(f"{t}(too short: {len(rows)} rows)")
            continue
        results.append(analyse(t, rows, dropped))

    # ---- console report ----------------------------------------------
    hdr = (f"{'ticker':<7}{'from':<12}{'to':<12}{'days':>6} "
           f"{'ON cum%':>14}{'ID cum%':>12} {'ON bps/d':>9}{'ID bps/d':>9} "
           f"{'ON t':>7}{'ID t':>7} {'ON Shp':>7}{'ID Shp':>7} {'oeq%':>6}")
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(results, key=lambda x: -x.overnight.mean_bps):
        print(f"{r.ticker:<7}{r.start:<12}{r.end:<12}{r.n_days:>6} "
              f"{r.overnight.cum_return_pct:>14,.1f}{r.intraday.cum_return_pct:>12,.1f} "
              f"{r.overnight.mean_bps:>9.2f}{r.intraday.mean_bps:>9.2f} "
              f"{r.overnight.t_stat:>7.2f}{r.intraday.t_stat:>7.2f} "
              f"{r.overnight.sharpe:>7.2f}{r.intraday.sharpe:>7.2f} "
              f"{r.open_eq_close_share*100:>6.1f}")
    if missing:
        print(f"\nmissing/skipped: {', '.join(missing)}")

    # cross-sectional summary
    n_on_gt = sum(1 for r in results if r.overnight.cum_return_pct > r.intraday.cum_return_pct)
    print(f"\novernight leg beat intraday leg in {n_on_gt}/{len(results)} names")

    out_path = os.path.join(args.out, "summary.json")
    with open(out_path, "w") as fh:
        json.dump({
            "generated_from": args.data_dir,
            "clip_start": args.start or None,
            "results": [asdict(r) for r in results],
            "missing": missing,
        }, fh, indent=1)
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
