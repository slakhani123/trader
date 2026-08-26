"""Multi-anchor point-in-time valuation engine.

How the score is built (plain English)
--------------------------------------
The instrument is valued from four independent angles, each scored 0-10
(10 = exceptionally cheap for what you get, 5 = fairly priced), then
blended with fixed weights:

* ``absolute`` (weight 30%) — yield-based attractiveness on today's price:
  earnings yield (0% -> 0, 10% -> 10), free-cash-flow yield (0% -> 0,
  8% -> 10) and, when a dividend is paid, dividend yield (0% -> 0,
  5% -> 10), averaged over whichever are computable. REITs use FFO yield
  (2%..10%) plus dividend yield instead of PE/FCF; banks use earnings
  yield plus tangible-book yield (1 / P/TBV, 0.5..1.4); insurers use
  earnings yield plus book yield (1 / P/B, 0.5..1.5).
* ``vs_history`` (25%) — percentile of today's primary multiple within its
  own point-in-time history. The history is reconstructed quarterly: at
  each past report's publication date the multiple is rebuilt from the
  then-last close, then-visible TTM fundamentals and then-reported diluted
  shares (no look-ahead). Score = 10 - percentile / 10, so trading at the
  bottom decile of one's own history scores ~9-10.
* ``vs_peers`` (25%) — average "expensiveness" percentile versus
  ``snapshot.peers`` across every multiple both sides report (P/E,
  EV/EBIT, EV/sales, P/B, and FCF yield inverted), again mapped as
  10 - percentile / 10. When an apparent discount coincides with clearly
  weaker TTM revenue growth (>3pp below peer median) or gross margin
  (>5pp below), the component is damped by 0.75 points and a
  contradicting evidence item notes the quality gap.
* ``scenario_asymmetry`` (20%) — upside/downside ratio
  (bull - price) / (price - bear) from the deterministic scenarios below,
  capped at 5 and mapped linearly (ratio 0.25 -> 0, ratio 3.0 -> 10).

A component that cannot be computed is set to neutral 5.0 with a warning
(its weight is kept, pulling the blend toward neutral rather than
inventing a view).

Scenario anchors (conservative, deterministic, local currency, 2dp):

* base — midpoint of (median own-history multiple x current TTM metric)
  and a reverse-DCF-lite fair value: present value of 5 years of TTM FCF
  per share grown at g (forward EPS growth from consensus, else the mean
  historical YoY growth of the primary metric, clamped to [-20%, +15%])
  discounted at 10%, plus a Gordon terminal value at 2% perpetual growth.
  TTM EPS substitutes when FCF is not positive. ``fair_value_low/high``
  are the min/max of the two base anchors (the honest disagreement band).
* bear — trough own-history multiple x the metric stressed by one standard
  deviation below its mean historical YoY growth (clamped to +/-50%).
* bull — 75th-percentile own-history multiple x the metric grown modestly
  (g capped at +10%).

The three scenarios are kept ordered: when the two base anchors disagree
strongly, bull is floored at base and bear is capped at base (the
adjustment is stated in the scenario rationale).

Value-trap guard: eight checks are recomputed locally (structural revenue
decline, margin collapse, excess leverage / refinancing wall, >3%/y
dilution, weak cash conversion, negative 90-day estimate revisions, no
catalyst within the medium horizon, cyclically-peak margins). When the
name looks cheap (any of absolute / vs_history / vs_peers >= 6.5) and two
or more checks fail, the final score is capped at 4.0.

EV multiples use EV/EBIT (operating income), never an EBITDA proxy, and
are labelled as such. Analyst price targets are summarised as evidence
only — they never enter fair value; a count below 5 or dispersion above
25% adds a warning and a contradicting reliability item when the target
would otherwise support the case.

Abstains when fewer than 4 quarterly reports are visible or market cap is
unknown.
"""

from __future__ import annotations

import math
import statistics
from datetime import timedelta

import numpy as np
import pandas as pd

from vigil.config import Settings
from vigil.engines.base import abstain, derived_ref, ev, sector_class
from vigil.indicators.stats import cagr, clamp, percentile_of, scale_linear
from vigil.schemas.core import (
    EngineResult,
    EstimateRecord,
    Evidence,
    FundamentalRecord,
    InstrumentSnapshot,
)

ENGINE = "valuation"

_WEIGHTS: dict[str, float] = {
    "absolute": 0.30,
    "vs_history": 0.25,
    "vs_peers": 0.25,
    "scenario_asymmetry": 0.20,
}

_MULTIPLE_LABEL: dict[str, str] = {
    "pe_ttm": "P/E (TTM)",
    "ev_ebit": "EV/EBIT (TTM)",
    "ev_sales": "EV/sales (TTM)",
    "pb": "P/B",
    "p_tbv": "P/TBV",
    "p_ffo": "P/FFO (TTM)",
}

# Multiples priced off enterprise value: fair equity value = m * metric - net debt.
_EV_TYPE = frozenset({"ev_ebit", "ev_sales"})

_DCF_RATE = 0.10
_DCF_TERMINAL_G = 0.02
_DCF_YEARS = 5


# ---------------------------------------------------------------------------
# Small numeric helpers (all pure, all None-safe)
# ---------------------------------------------------------------------------


def _ttm(qs: list[FundamentalRecord], fieldname: str, offset: int = 0) -> float | None:
    """Sum of a flow field over the four quarters ending ``offset`` quarters ago."""
    hi = len(qs) - offset
    lo = hi - 4
    if lo < 0:
        return None
    vals = [getattr(q, fieldname) for q in qs[lo:hi]]
    if any(v is None for v in vals):
        return None
    return float(sum(vals))


def _ttm_fcf(qs: list[FundamentalRecord]) -> float | None:
    if len(qs) < 4:
        return None
    vals = [q.free_cash_flow for q in qs[-4:]]
    if any(v is None for v in vals):
        return None
    return float(sum(v for v in vals if v is not None))


def _tangible_book(latest: FundamentalRecord, shares: float | None) -> float | None:
    tbps = latest.sector_metrics.get("tangible_book_per_share")
    if tbps is not None and shares:
        return float(tbps * shares)
    if latest.total_equity is not None and latest.goodwill_intangibles is not None:
        return float(latest.total_equity - latest.goodwill_intangibles)
    return None


def _ffo_ttm(qs: list[FundamentalRecord]) -> float | None:
    if len(qs) < 4:
        return None
    vals = [q.sector_metrics.get("ffo") for q in qs[-4:]]
    if any(v is None for v in vals):
        return None
    return float(sum(v for v in vals if v is not None))


def _net_debt(latest: FundamentalRecord) -> float | None:
    if latest.total_debt is None or latest.cash_and_equivalents is None:
        return None
    return float(latest.total_debt - latest.cash_and_equivalents)


def _multiple_value(
    name: str,
    price: float,
    shares: float,
    last4: list[FundamentalRecord],
    latest: FundamentalRecord,
) -> float | None:
    """Compute one price multiple from a price, share count and 4 quarters."""
    if price <= 0 or shares <= 0:
        return None
    mcap = price * shares
    if name == "pe_ttm":
        ni = _ttm(last4, "net_income")
        return mcap / ni if ni is not None and ni > 0 else None
    if name == "pb":
        eq = latest.total_equity
        return mcap / eq if eq is not None and eq > 0 else None
    if name == "p_tbv":
        tb = _tangible_book(latest, shares)
        return mcap / tb if tb is not None and tb > 0 else None
    if name == "p_ffo":
        ffo = _ffo_ttm(last4)
        return mcap / ffo if ffo is not None and ffo > 0 else None
    if name in _EV_TYPE:
        nd = _net_debt(latest)
        if nd is None:
            return None
        enterprise = mcap + nd
        denom = _ttm(last4, "operating_income" if name == "ev_ebit" else "revenue")
        return enterprise / denom if denom is not None and denom > 0 else None
    return None


def _pick_primary(sc: str, qs: list[FundamentalRecord], shares: float | None) -> str:
    latest = qs[-1]
    if sc == "bank":
        if _tangible_book(latest, shares) is not None:
            return "p_tbv"
        ni = _ttm(qs, "net_income")
        return "pe_ttm" if ni is not None and ni > 0 else "pb"
    if sc == "insurer":
        if latest.total_equity is not None and latest.total_equity > 0:
            return "pb"
        return "pe_ttm"
    if sc == "reit":
        return "p_ffo" if _ffo_ttm(qs) is not None else "pb"
    ni = _ttm(qs, "net_income")
    if ni is not None and ni > 0:
        return "pe_ttm"
    op = _ttm(qs, "operating_income")
    if op is not None and op > 0 and _net_debt(latest) is not None:
        return "ev_ebit"
    rev = _ttm(qs, "revenue")
    if rev is not None and rev > 0 and _net_debt(latest) is not None:
        return "ev_sales"
    return "pb"


# ---------------------------------------------------------------------------
# Point-in-time history of the primary multiple
# ---------------------------------------------------------------------------


def _history_series(
    snapshot: InstrumentSnapshot,
    name: str,
    qs: list[FundamentalRecord],
    current_value: float | None,
) -> list[float]:
    """Quarterly PIT series of ``name``: at each report's publication date,
    rebuild the multiple from the then-last close and then-visible records.
    The current value (today's) is appended as the final point."""
    out: list[float] = []
    if snapshot.prices.empty:
        return [current_value] if current_value is not None else []
    closes = snapshot.prices["close"].dropna()
    for i in range(3, len(qs)):
        t = pd.Timestamp(qs[i].published_at)
        visible = [q for q in qs if pd.Timestamp(q.published_at) <= t]
        if len(visible) < 4:
            continue
        px = closes.loc[:t]
        if px.empty:
            continue
        price = float(px.iloc[-1])
        latest = visible[-1]
        shares = latest.shares_diluted or snapshot.info.shares_outstanding
        if not shares or shares <= 0:
            continue
        m = _multiple_value(name, price, float(shares), visible[-4:], latest)
        if m is not None and m > 0:
            out.append(m)
    if current_value is not None and current_value > 0:
        out.append(current_value)
    return out


# ---------------------------------------------------------------------------
# Estimates / growth
# ---------------------------------------------------------------------------


def _forward_eps_estimate(snapshot: InstrumentSnapshot) -> EstimateRecord | None:
    """The nearest forward-period EPS consensus (period_end after as_of)."""
    cands = [
        e for e in snapshot.estimates if e.metric == "eps" and e.period_end > snapshot.as_of
    ]
    if not cands:
        return None
    cands.sort(key=lambda e: (e.period_end, e.as_of))
    return cands[0]


def _forward_growth(snapshot: InstrumentSnapshot, est: EstimateRecord | None) -> float | None:
    if est is None:
        return None
    ttm_eps = snapshot.ttm_sum("eps_diluted")
    if ttm_eps is None or ttm_eps <= 0:
        return None
    return float(est.mean / ttm_eps - 1.0)


def _metric_growth_stats(values: list[float]) -> tuple[float, float] | None:
    """Mean and std of YoY growth of a quarterly metric series (level or
    TTM). Needs at least 3 YoY observations."""
    growths = [
        values[i] / values[i - 4] - 1.0
        for i in range(4, len(values))
        if values[i - 4] > 0
    ]
    if len(growths) < 3:
        return None
    mean = float(np.mean(growths))
    std = float(np.std(growths))
    return mean, std


def _primary_metric_series(name: str, qs: list[FundamentalRecord]) -> list[float]:
    """Quarterly series of the primary multiple's denominator: TTM sums for
    flow metrics, reported levels for stock metrics."""
    if name in {"pb", "p_tbv"}:
        out = []
        for q in qs:
            v = q.total_equity if name == "pb" else _tangible_book(q, q.shares_diluted)
            if v is not None and v > 0:
                out.append(float(v))
        return out
    fieldmap = {"pe_ttm": "net_income", "ev_ebit": "operating_income", "ev_sales": "revenue"}
    if name == "p_ffo":
        flows = [q.sector_metrics.get("ffo") for q in qs]
    else:
        flows = [getattr(q, fieldmap[name]) for q in qs]
    out = []
    for i in range(3, len(flows)):
        window = flows[i - 3 : i + 1]
        if any(v is None for v in window):
            continue
        total = float(sum(v for v in window if v is not None))
        if total > 0:
            out.append(total)
    return out


def _reverse_dcf(per_share: float, growth: float) -> float:
    """Reverse-DCF-lite: PV of 5 years of the per-share flow grown at
    ``growth`` (clamped to [-20%, +15%]), discounted at 10%, plus a Gordon
    terminal value at 2% perpetual growth."""
    g = clamp(growth, -0.20, 0.15)
    pv, cf = 0.0, per_share
    for t in range(1, _DCF_YEARS + 1):
        cf *= 1.0 + g
        pv += cf / (1.0 + _DCF_RATE) ** t
    terminal = cf * (1.0 + _DCF_TERMINAL_G) / (_DCF_RATE - _DCF_TERMINAL_G)
    pv += terminal / (1.0 + _DCF_RATE) ** _DCF_YEARS
    return pv


def _price_from_multiple(
    name: str, multiple: float, metric_total: float, shares: float, net_debt: float | None
) -> float | None:
    """Equity price per share implied by ``multiple`` on ``metric_total``."""
    if shares <= 0:
        return None
    if name in _EV_TYPE:
        if net_debt is None:
            return None
        return max(0.0, (multiple * metric_total - net_debt) / shares)
    return max(0.0, multiple * metric_total / shares)


# ---------------------------------------------------------------------------
# Value-trap checks (recomputed locally — the quality engine is NOT imported)
# ---------------------------------------------------------------------------


def _value_trap_checks(
    snapshot: InstrumentSnapshot, settings: Settings, qs: list[FundamentalRecord]
) -> list[str]:
    failed: list[str] = []

    # 1. Structural revenue decline: TTM < TTM 2y ago AND negative ~3y CAGR.
    rev_now = _ttm(qs, "revenue")
    rev_2y = _ttm(qs, "revenue", offset=8)
    back = min(12, len(qs) - 4)
    rev_back = _ttm(qs, "revenue", offset=back) if back >= 8 else None
    if rev_now is not None and rev_2y is not None and rev_back is not None:
        growth = cagr(rev_back, rev_now, back / 4.0)
        if rev_now < rev_2y and growth is not None and growth < 0:
            failed.append("structural_revenue_decline")

    # 2. Margin collapse: TTM operating margin down >40% vs its ~3y average.
    op_now, margin_hist = _ttm(qs, "operating_income"), []
    for q in qs[-12:]:
        if q.operating_income is not None and q.revenue:
            margin_hist.append(q.operating_income / q.revenue)
    if op_now is not None and rev_now and len(margin_hist) >= 8:
        cur_margin = op_now / rev_now
        avg = float(np.mean(margin_hist))
        if avg > 0 and cur_margin < 0.6 * avg:
            failed.append("margin_collapse")

    # 3. Excess leverage: net debt / TTM EBIT > 5, or a refinancing wall
    #    (debt due within 1y exceeds cash plus positive TTM FCF).
    latest = qs[-1]
    nd = _net_debt(latest)
    fcf = _ttm_fcf(qs)
    leveraged = op_now is not None and op_now > 0 and nd is not None and nd / op_now > 5.0
    wall = (
        latest.debt_due_within_1y is not None
        and latest.cash_and_equivalents is not None
        and latest.debt_due_within_1y
        > latest.cash_and_equivalents + max(fcf or 0.0, 0.0)
    )
    if leveraged or wall:
        failed.append("excess_leverage")

    # 4. Dilution above 3%/year (diluted shares vs 4 quarters earlier).
    if len(qs) >= 5:
        s_now, s_prev = qs[-1].shares_diluted, qs[-5].shares_diluted
        diluting = (
            s_now is not None and s_prev is not None and s_prev > 0
            and s_now / s_prev - 1.0 > 0.03
        )
        if diluting:
            failed.append("dilution")

    # 5. Weak cash conversion: 4-quarter OCF / NI below 0.6 (NI positive).
    ocf4, ni4 = _ttm(qs, "operating_cash_flow"), _ttm(qs, "net_income")
    if ocf4 is not None and ni4 is not None and ni4 > 0 and ocf4 / ni4 < 0.6:
        failed.append("weak_cash_conversion")

    # 6. Estimate revisions net negative over 90 days.
    est = _forward_eps_estimate(snapshot)
    if est is not None:
        if est.mean_90d_ago is not None and est.mean_90d_ago != 0:
            if (est.mean - est.mean_90d_ago) / abs(est.mean_90d_ago) < -0.01:
                failed.append("negative_estimate_revisions")
        elif est.down_revisions_30d > est.up_revisions_30d:
            failed.append("negative_estimate_revisions")

    # 7. No unresolved catalyst within the medium horizon (~calendar days).
    window_days = int(settings.horizons.medium_max_days * 7 / 5)
    horizon_end = snapshot.as_of + timedelta(days=window_days)
    upcoming = [
        c
        for c in snapshot.catalysts
        if not c.resolved and snapshot.as_of <= c.expected_date <= horizon_end
    ]
    if not upcoming:
        failed.append("no_catalyst_within_horizon")

    # 8. Cyclically-peak earnings: TTM op margin > 1.5x its own 5y median.
    margins_5y = [
        q.operating_income / q.revenue
        for q in qs[-20:]
        if q.operating_income is not None and q.revenue
    ]
    if op_now is not None and rev_now and len(margins_5y) >= 12:
        med = statistics.median(margins_5y)
        if med > 0 and (op_now / rev_now) > 1.5 * med:
            failed.append("cyclical_peak_earnings")

    return failed


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


def analyse(snapshot: InstrumentSnapshot, settings: Settings) -> EngineResult:
    qs = snapshot.quarterlies()
    if len(qs) < 4:
        return abstain(ENGINE, f"only {len(qs)} quarterly reports visible (need 4)")
    price = snapshot.last_close
    mcap = snapshot.market_cap_local() or snapshot.liquidity.market_cap_local
    if price is None or price <= 0 or mcap is None or mcap <= 0:
        return abstain(ENGINE, "market cap unknown (missing price or share count)", 0.2)

    warnings: list[str] = []
    evidence: list[Evidence] = []
    shares_now = float(snapshot.info.shares_outstanding or (mcap / price))
    last4, latest = qs[-4:], qs[-1]
    sc = sector_class(snapshot)
    fund_ref = derived_ref(snapshot, "valuation_multiples", based_on=latest.source)

    # --- multiples ---------------------------------------------------------
    ni_ttm = _ttm(qs, "net_income")
    rev_ttm = _ttm(qs, "revenue")
    op_ttm = _ttm(qs, "operating_income")
    fcf_ttm = _ttm_fcf(qs)
    div_ttm = _ttm(qs, "dividends_paid")
    nd = _net_debt(latest)
    if nd is None:
        warnings.append("debt/cash not reported — EV multiples unavailable")

    multiples: dict[str, float | None] = {
        name: _multiple_value(name, price, shares_now, last4, latest)
        for name in ("pe_ttm", "ev_ebit", "ev_sales", "pb")
    }
    multiples["earnings_yield"] = ni_ttm / mcap if ni_ttm is not None else None
    multiples["fcf_yield"] = fcf_ttm / mcap if fcf_ttm is not None else None
    multiples["dividend_yield"] = (
        abs(div_ttm) / mcap if div_ttm is not None and div_ttm != 0 else None
    )
    if sc == "bank":
        multiples["p_tbv"] = _multiple_value("p_tbv", price, shares_now, last4, latest)
    if sc == "reit":
        multiples["p_ffo"] = _multiple_value("p_ffo", price, shares_now, last4, latest)

    est = _forward_eps_estimate(snapshot)
    fwd_growth = _forward_growth(snapshot, est)
    pe = multiples.get("pe_ttm")
    multiples["peg"] = (
        pe / (fwd_growth * 100.0)
        if pe is not None and fwd_growth is not None and fwd_growth > 0
        else None
    )

    primary = _pick_primary(sc, qs, shares_now)
    primary_value = multiples.get(primary)
    if primary_value is None:
        primary_value = _multiple_value(primary, price, shares_now, last4, latest)
        multiples[primary] = primary_value
    label = _MULTIPLE_LABEL[primary]

    # --- absolute (yield-based) --------------------------------------------
    ey = multiples["earnings_yield"]
    fy = multiples["fcf_yield"]
    dy = multiples["dividend_yield"]
    subs: list[float] = []
    if sc == "reit":
        ffo = _ffo_ttm(qs)
        ffo_yield = ffo / mcap if ffo is not None else None
        if ffo_yield is not None:
            subs.append(scale_linear(ffo_yield, 0.02, 0.10))
        if dy is not None:
            subs.append(scale_linear(dy, 0.0, 0.07))
    elif sc == "bank":
        if ey is not None:
            subs.append(scale_linear(ey, 0.0, 0.10))
        ptbv = multiples.get("p_tbv")
        if ptbv is not None and ptbv > 0:
            subs.append(scale_linear(1.0 / ptbv, 0.5, 1.4))
    elif sc == "insurer":
        if ey is not None:
            subs.append(scale_linear(ey, 0.0, 0.10))
        pb = multiples.get("pb")
        if pb is not None and pb > 0:
            subs.append(scale_linear(1.0 / pb, 0.5, 1.5))
    else:
        if ey is not None:
            subs.append(scale_linear(ey, 0.0, 0.10))
        if fy is not None:
            subs.append(scale_linear(fy, 0.0, 0.08))
        if dy is not None and dy > 0:
            subs.append(scale_linear(dy, 0.0, 0.05))
    if subs:
        absolute = float(np.mean(subs))
    else:
        absolute = 5.0
        warnings.append("no yield computable — absolute component neutral")

    # --- vs own history -----------------------------------------------------
    hist = _history_series(snapshot, primary, qs, primary_value)
    hist_pct: float | None = None
    if primary_value is not None and len(hist) >= 4:
        hist_pct = percentile_of(primary_value, hist)
    if hist_pct is not None:
        vs_history = scale_linear(hist_pct, 100.0, 0.0)
    else:
        vs_history = 5.0
        warnings.append("own-history multiple series too short — vs_history neutral")

    # --- vs peers -----------------------------------------------------------
    expensiveness: list[float] = []
    for key in ("pe_ttm", "ev_ebit", "ev_sales", "pb"):
        ours = multiples.get(key)
        pop = [p.metrics[key] for p in snapshot.peers if key in p.metrics]
        if ours is not None:
            pct = percentile_of(ours, pop)
            if pct is not None:
                expensiveness.append(pct)
    if fy is not None:
        pop = [p.metrics["fcf_yield"] for p in snapshot.peers if "fcf_yield" in p.metrics]
        pct = percentile_of(fy, pop)
        if pct is not None:
            expensiveness.append(100.0 - pct)
    peer_pct = float(np.mean(expensiveness)) if expensiveness else None
    quality_gap: list[str] = []
    if peer_pct is not None:
        vs_peers = scale_linear(peer_pct, 100.0, 0.0)
        if vs_peers >= 6.5:
            gms = [p.metrics["gross_margin"] for p in snapshot.peers if "gross_margin" in p.metrics]
            grs = [
                p.metrics["revenue_growth_ttm"]
                for p in snapshot.peers
                if "revenue_growth_ttm" in p.metrics
            ]
            gp_ttm = _ttm(qs, "gross_profit")
            our_gm = gp_ttm / rev_ttm if gp_ttm is not None and rev_ttm else None
            rev_prev = _ttm(qs, "revenue", offset=4)
            our_gr = rev_ttm / rev_prev - 1.0 if rev_ttm and rev_prev else None
            if grs and our_gr is not None and our_gr < statistics.median(grs) - 0.03:
                quality_gap.append("slower TTM revenue growth than peers")
            if gms and our_gm is not None and our_gm < statistics.median(gms) - 0.05:
                quality_gap.append("lower gross margin than peers")
            if quality_gap:
                vs_peers = clamp(vs_peers - 0.75)
    else:
        vs_peers = 5.0
        warnings.append("no comparable peer multiples — vs_peers neutral")

    # --- scenarios -----------------------------------------------------------
    metric_map: dict[str, float | None] = {
        "pe_ttm": ni_ttm,
        "ev_ebit": op_ttm,
        "ev_sales": rev_ttm,
        "pb": latest.total_equity,
        "p_tbv": _tangible_book(latest, shares_now),
        "p_ffo": _ffo_ttm(qs),
    }
    metric_now = metric_map.get(primary)
    metric_series = _primary_metric_series(primary, qs)
    gstats = _metric_growth_stats(metric_series)
    growth = fwd_growth if fwd_growth is not None else (gstats[0] if gstats else None)
    if growth is None:
        warnings.append("no growth estimate available — scenarios assume zero growth")
        growth = 0.0

    scenarios: dict[str, dict[str, float | str]] | None = None
    fair_value_low: float | None = None
    fair_value_high: float | None = None
    asymmetry_ratio: float | None = None
    hist_ex = [h for h in hist if h > 0]
    if (
        metric_now is not None
        and metric_now > 0
        and len(hist_ex) >= 4
        and gstats is not None
    ):
        median_m = float(np.percentile(hist_ex, 50))
        p75_m = float(np.percentile(hist_ex, 75))
        trough_m = float(min(hist_ex))
        base_anchor = _price_from_multiple(primary, median_m, metric_now, shares_now, nd)
        per_share = None
        if fcf_ttm is not None and fcf_ttm > 0:
            per_share = fcf_ttm / shares_now
        elif ni_ttm is not None and ni_ttm > 0:
            per_share = ni_ttm / shares_now
        dcf_anchor = _reverse_dcf(per_share, growth) if per_share is not None else None
        stress = clamp(gstats[0] - gstats[1], -0.5, 0.5)
        bull_g = clamp(growth, 0.0, 0.10)
        bear_px = _price_from_multiple(
            primary, trough_m, metric_now * (1.0 + stress), shares_now, nd
        )
        bull_px = _price_from_multiple(
            primary, p75_m, metric_now * (1.0 + bull_g), shares_now, nd
        )
        if base_anchor is not None and bear_px is not None and bull_px is not None:
            anchors = [base_anchor] + ([dcf_anchor] if dcf_anchor is not None else [])
            base_px = float(np.mean(anchors))
            fair_value_low = round(min(anchors), 2)
            fair_value_high = round(max(anchors), 2)
            base_rat = (
                f"Mid of median own-history {label} {median_m:.1f}x on the current TTM "
                f"metric and reverse-DCF-lite (10% discount, {growth:+.0%} 5y growth, "
                f"2% terminal)"
                if dcf_anchor is not None
                else f"Median own-history {label} {median_m:.1f}x on the current TTM metric"
            )
            bull_rat = (
                f"75th-percentile own-history {label} {p75_m:.1f}x on the metric "
                f"grown {bull_g:+.0%}"
            )
            bear_rat = (
                f"Trough own-history {label} {trough_m:.1f}x on the metric "
                f"stressed {stress:+.0%} (mean-1sigma of historical YoY growth)"
            )
            if bull_px < base_px:  # keep scenarios ordered (documented guard)
                bull_px = base_px
                bull_rat += "; floored at the base anchor"
            if bear_px > base_px:
                bear_px = base_px
                bear_rat += "; capped at the base anchor"
            scenarios = {
                "base": {"price": round(base_px, 2), "rationale": base_rat},
                "bull": {"price": round(bull_px, 2), "rationale": bull_rat},
                "bear": {"price": round(bear_px, 2), "rationale": bear_rat},
            }
            upside = bull_px - price
            downside = price - bear_px
            if downside <= 0:
                asymmetry_ratio = 5.0 if upside > 0 else 0.0
            elif upside <= 0:
                asymmetry_ratio = 0.0
            else:
                asymmetry_ratio = float(min(upside / downside, 5.0))
    if asymmetry_ratio is not None:
        scenario_asymmetry = scale_linear(asymmetry_ratio, 0.25, 3.0)
    else:
        scenario_asymmetry = 5.0
        warnings.append("insufficient history for scenarios — asymmetry component neutral")

    # --- value trap -----------------------------------------------------------
    failed = _value_trap_checks(snapshot, settings, qs)
    value_trap = {"is_trap_risk": len(failed) >= 2, "failed_checks": failed}

    # --- blend + trap cap -------------------------------------------------------
    components = {
        "absolute": round(absolute, 2),
        "vs_history": round(vs_history, 2),
        "vs_peers": round(vs_peers, 2),
        "scenario_asymmetry": round(scenario_asymmetry, 2),
    }
    score = clamp(sum(components[k] * w for k, w in _WEIGHTS.items()))
    looks_cheap = any(components[k] >= 6.5 for k in ("absolute", "vs_history", "vs_peers"))
    trap_capped = looks_cheap and len(failed) >= 2
    if trap_capped:
        score = min(score, 4.0)
        warnings.append(
            f"cheap multiple with {len(failed)} value-trap flags — score capped at 4"
        )

    # --- analyst targets (evidence only) ----------------------------------------
    target = snapshot.target
    target_summary: dict[str, float | int | None] | None = None
    if target is not None:
        upside_pct = (target.mean / price - 1.0) * 100.0
        dispersion_pct = (
            target.std / target.mean * 100.0 if target.std is not None and target.mean else None
        )
        target_summary = {
            "mean": round(target.mean, 2),
            "implied_upside_pct": round(upside_pct, 1),
            "count": target.analyst_count,
            "dispersion_pct": round(dispersion_pct, 1) if dispersion_pct is not None else None,
            "median_age_days": target.median_age_days,
        }
        unreliable: list[str] = []
        if target.analyst_count < 5:
            unreliable.append(f"only {target.analyst_count} analyst targets")
        if dispersion_pct is not None and dispersion_pct > 25.0:
            unreliable.append(f"target dispersion {dispersion_pct:.0f}% of mean")
        if unreliable:
            warnings.append("analyst targets unreliable: " + "; ".join(unreliable))
        direction = "neutral"
        if not unreliable and upside_pct > 10.0:
            direction = "supports"
        elif upside_pct < -10.0:
            direction = "contradicts"
        drift = ""
        if target.mean_30d_ago:
            drift_pct = (target.mean / target.mean_30d_ago - 1.0) * 100.0
            drift = f", 30d drift {drift_pct:+.1f}%"
        evidence.append(
            ev(
                snapshot,
                "target_implied_upside",
                (
                    f"Consensus target {target.mean:.2f} implies {upside_pct:+.1f}% "
                    f"({target.analyst_count} analysts{drift}) — evidence only, "
                    f"not used in fair value"
                ),
                round(upside_pct, 1),
                direction,  # type: ignore[arg-type]
                "valuation",
                target.source,
            )
        )
        if unreliable and upside_pct > 10.0:
            evidence.append(
                ev(
                    snapshot,
                    "target_reliability",
                    "Analyst target looks supportive but is unreliable: "
                    + "; ".join(unreliable),
                    None,
                    "contradicts",
                    "valuation",
                    target.source,
                )
            )

    # --- entry zone hint ----------------------------------------------------------
    entry_zone_hint: dict[str, float] | None = None
    if score >= 6.5 and not trap_capped and fair_value_low is not None:
        # Floor (not round) so the band never pokes above the current price.
        zone_high = math.floor(min(price, fair_value_low) * 100) / 100
        if zone_high > 0:
            entry_zone_hint = {"low": round(zone_high * 0.93, 2), "high": zone_high}

    # --- evidence ------------------------------------------------------------------
    if primary_value is not None:
        pdir = "supports" if absolute >= 6.5 else "contradicts" if absolute <= 3.5 else "neutral"
        evidence.append(
            ev(
                snapshot,
                f"{primary}_current",
                f"{label} is {primary_value:.1f}x",
                round(primary_value, 2),
                pdir,  # type: ignore[arg-type]
                "valuation",
                fund_ref,
            )
        )
    if ey is not None:
        d = "supports" if ey >= 0.08 else "contradicts" if ey < 0.02 else "neutral"
        evidence.append(
            ev(
                snapshot,
                "earnings_yield_ttm",
                f"Earnings yield (TTM) is {ey:.1%}",
                round(ey * 100, 2),
                d,  # type: ignore[arg-type]
                "valuation",
                derived_ref(snapshot, "earnings_yield_ttm", based_on=latest.source),
            )
        )
    if fy is not None:
        d = "supports" if fy >= 0.06 else "contradicts" if fy < 0.01 else "neutral"
        evidence.append(
            ev(
                snapshot,
                "fcf_yield_ttm",
                f"Free-cash-flow yield (TTM) is {fy:.1%}",
                round(fy * 100, 2),
                d,  # type: ignore[arg-type]
                "valuation",
                derived_ref(snapshot, "fcf_yield_ttm", based_on=latest.source),
            )
        )
    if dy is not None and dy > 0:
        evidence.append(
            ev(
                snapshot,
                "dividend_yield_ttm",
                f"Dividend yield (TTM) is {dy:.1%}",
                round(dy * 100, 2),
                "supports" if dy >= 0.04 else "neutral",
                "valuation",
                derived_ref(snapshot, "dividend_yield_ttm", based_on=latest.source),
            )
        )
    if hist_pct is not None:
        d = "supports" if hist_pct <= 25 else "contradicts" if hist_pct >= 75 else "neutral"
        evidence.append(
            ev(
                snapshot,
                "multiple_history_percentile",
                (
                    f"{label} sits at the {hist_pct:.0f}th percentile of its own "
                    f"point-in-time history ({len(hist)} quarterly points)"
                ),
                round(hist_pct, 1),
                d,  # type: ignore[arg-type]
                "valuation",
                derived_ref(snapshot, "multiple_history_percentile", based_on=latest.source),
            )
        )
    if peer_pct is not None:
        d = "supports" if peer_pct <= 25 else "contradicts" if peer_pct >= 75 else "neutral"
        evidence.append(
            ev(
                snapshot,
                "peer_relative_multiple",
                (
                    f"Average expensiveness vs {len(snapshot.peers)} peers is the "
                    f"{peer_pct:.0f}th percentile across shared multiples"
                ),
                round(peer_pct, 1),
                d,  # type: ignore[arg-type]
                "valuation",
                derived_ref(snapshot, "peer_relative_multiple"),
            )
        )
    if quality_gap:
        evidence.append(
            ev(
                snapshot,
                "peer_discount_quality_gap",
                "Peer discount is partly explained by " + " and ".join(quality_gap),
                None,
                "contradicts",
                "valuation",
                derived_ref(snapshot, "peer_relative_multiple"),
            )
        )
    if scenarios is not None:
        base_px = float(scenarios["base"]["price"])  # type: ignore[arg-type]
        gap = (base_px / price - 1.0) * 100.0
        d = "supports" if gap > 15 else "contradicts" if gap < -10 else "neutral"
        evidence.append(
            ev(
                snapshot,
                "base_fair_value_gap",
                (
                    f"Base scenario fair value {base_px:.2f} vs price {price:.2f} "
                    f"({gap:+.1f}%); bull/bear asymmetry {asymmetry_ratio:.1f}x"
                ),
                round(gap, 1),
                d,  # type: ignore[arg-type]
                "valuation",
                derived_ref(snapshot, "valuation_scenarios", based_on=latest.source),
            )
        )
    trap_dir = "contradicts" if len(failed) >= 2 else "neutral" if failed else "supports"
    trap_stmt = (
        f"Value-trap checks failed: {', '.join(failed)}"
        if failed
        else "No value-trap checks failed"
    )
    evidence.append(
        ev(
            snapshot,
            "value_trap_checks",
            trap_stmt,
            float(len(failed)),
            trap_dir,  # type: ignore[arg-type]
            "valuation",
            derived_ref(snapshot, "value_trap_checks", based_on=latest.source),
        )
    )
    evidence = evidence[:12]

    # --- data quality -------------------------------------------------------------
    checks = [
        len(qs) >= 8,
        nd is not None,
        est is not None,
        peer_pct is not None,
        target is not None,
        snapshot.liquidity.price_staleness_days <= 3,
        len(hist) >= 8,
    ]
    data_quality = clamp((3 + sum(checks)) / (3 + len(checks)), 0.0, 1.0)

    details: dict[str, object] = {
        "scenarios": scenarios,
        "fair_value_low": fair_value_low,
        "fair_value_high": fair_value_high,
        "primary_multiple": primary,
        "multiples": {
            k: (round(v, 4) if v is not None else None) for k, v in multiples.items()
        },
        "value_trap": value_trap,
        "target_summary": target_summary,
        "entry_zone_hint": entry_zone_hint,
        "sector_class": sc,
        "history_percentile": round(hist_pct, 1) if hist_pct is not None else None,
        "peer_percentile": round(peer_pct, 1) if peer_pct is not None else None,
        "trap_capped": trap_capped,
    }
    return EngineResult(
        engine=ENGINE,
        score=round(score, 2),
        components=components,
        evidence=evidence,
        warnings=warnings,
        data_quality=round(data_quality, 3),
        details=details,
    )
