"""Sector-aware fundamental quality engine.

Score construction (plain English)
----------------------------------
Five sub-scores, each 0-10 (10 = excellent, 5 = neutral), are computed from
point-in-time quarterly fundamentals and blended into the engine score with
sector-dependent weights. When a sub-score cannot be computed from the data,
its weight is dropped and the remaining weights renormalise (a warning is
added; the components dict then shows a neutral 5.0 for that entry).

* ``general`` / ``commodity``: quality 30%, growth 25%, balance sheet 20%,
  cash quality 15%, shareholder 10%.
* ``bank`` / ``insurer``: quality 40%, growth 25%, balance sheet (capital
  adequacy) 25%, shareholder 10%. Cash quality carries no weight and is
  reported neutrally at 5.0 — accrual metrics are not meaningful for
  lenders, and FCF/capex/EV metrics are never used for them.
* ``reit``: quality 30%, growth 20%, balance sheet (LTV bands) 25%,
  cash quality 10%, shareholder (dividends vs FFO) 15%. High absolute debt
  is not punished beyond the LTV bands.
* ``early_stage``: growth 45%, balance sheet (cash runway) 25%,
  shareholder 15%, quality (margin trajectory) 10%, cash quality 5%.
  The final score is capped at 6.0 with a warning — durable quality is
  unprovable pre-profit.

What each sub-score measures:

* ``quality`` — ROIC (NOPAT / (equity + debt - cash)), ROE, margin level
  and 3-year margin trend, incremental ROIC (+-0.5 adjustment). Banks use
  ROE / net interest margin / operating margin; insurers ROE-led; REITs
  occupancy / FFO margin; commodities score margins against their own
  full-history median (mid-cycle) and flag peak-margin risk instead of
  extrapolating. Customer concentration above 20% deducts up to 2 points.
* ``growth`` — TTM revenue / EPS / FCF growth YoY, 3-year CAGRs,
  persistence (share of quarters moving with the trend) and acceleration
  (+-0.75 when growth is speeding up / rolling over). Banks add tangible
  book value per share growth; insurers book value per share; REITs FFO
  per share.
* ``balance_sheet`` — net debt / TTM EBIT, interest coverage, current
  ratio, and a refinancing-wall check (debt due within one year exceeding
  both cash and 15% of total debt). Banks/insurers score CET1 (or
  equity/assets) and loan-loss provisions instead — leverage rules do not
  apply. REITs score LTV bands. Early-stage names score cash runway in
  years of current FCF burn.
* ``cash_quality`` — OCF/NI over four quarters, accruals
  ((NI - OCF) / assets), and receivables growth discipline vs revenue.
* ``shareholder`` — one-year dilution from the diluted share count
  (split-adjusted), SBC / revenue, payout (dividends + buybacks) vs TTM
  FCF (banks/insurers: vs net income; REITs: vs FFO), and insider
  activity in the last 180 days (cluster buys support, heavy selling
  contradicts).

Accounting red flags — a restatement published within ~18 months, an
auditor change between consecutive reports, receivables or inventory
growth outpacing revenue by more than 1.5x (with a 5pp materiality floor)
over four quarters, 'adjusted' profit exclusions above 20% of |net income|
in at least 3 of the last 6 quarters, and a sustained 4-quarter OCF/NI
below 0.7 — each subtract 0.4 points from the blended score (capped at
-2.0 total) and appear in ``details.red_flags`` with contradicting
evidence.

Documented proxies — never silent substitutions:

* The data model carries no D&A, so ``details.net_debt_ebitda`` and
  interest coverage are computed on TTM EBIT (operating income) and every
  statement labels it as such. EBIT understates EBITDA, so the leverage
  reading errs on the conservative side.
* NOPAT uses the effective tax rate implied by net income / pre-tax income
  when computable (clamped to 0-40%), otherwise a 25% standard assumption.

Abstains when fewer than four quarterly reports are visible.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from statistics import fmean, median

from vigil.config import Settings
from vigil.engines.base import abstain, derived_ref, ev, sector_class
from vigil.indicators.stats import cagr, clamp, scale_linear, trend_consistency
from vigil.schemas.core import (
    Direction,
    EngineResult,
    Evidence,
    FundamentalRecord,
    InstrumentSnapshot,
    Pillar,
    SourceRef,
)

_ENGINE = "quality"
_TAX_FALLBACK = 0.25
_RED_FLAG_PENALTY = 0.4
_RED_FLAG_PENALTY_CAP = 2.0
_EARLY_STAGE_CAP = 6.0
_INSIDER_WINDOW_DAYS = 180
_RESTATEMENT_WINDOW_DAYS = 548  # ~18 months
_CONCENTRATION_PCT = 20.0

_WEIGHTS: dict[str, dict[str, float]] = {
    "general": {
        "quality": 0.30, "growth": 0.25, "balance_sheet": 0.20,
        "cash_quality": 0.15, "shareholder": 0.10,
    },
    "commodity": {
        "quality": 0.30, "growth": 0.25, "balance_sheet": 0.20,
        "cash_quality": 0.15, "shareholder": 0.10,
    },
    "bank": {
        "quality": 0.40, "growth": 0.25, "balance_sheet": 0.25,
        "cash_quality": 0.0, "shareholder": 0.10,
    },
    "insurer": {
        "quality": 0.40, "growth": 0.25, "balance_sheet": 0.25,
        "cash_quality": 0.0, "shareholder": 0.10,
    },
    "reit": {
        "quality": 0.30, "growth": 0.20, "balance_sheet": 0.25,
        "cash_quality": 0.10, "shareholder": 0.15,
    },
    "early_stage": {
        "quality": 0.10, "growth": 0.45, "balance_sheet": 0.25,
        "cash_quality": 0.05, "shareholder": 0.15,
    },
}


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def _ttm(series: list[float | None]) -> list[float | None]:
    """Rolling 4-quarter sums; entry i aligns with quarter i+3 of the input."""
    out: list[float | None] = []
    for i in range(3, len(series)):
        window = [v for v in series[i - 3 : i + 1] if v is not None]
        out.append(float(sum(window)) if len(window) == 4 else None)
    return out


def _at(series: list[float | None], back: int = 0) -> float | None:
    idx = len(series) - 1 - back
    if idx < 0:
        return None
    return series[idx]


def _growth(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or prev <= 0:
        return None
    return cur / prev - 1.0


def _ratio(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return num / den


def _wmean(parts: list[tuple[float, float | None]]) -> float | None:
    total = weight = 0.0
    for w, s in parts:
        if s is None or w <= 0:
            continue
        total += w * s
        weight += w
    if weight <= 0:
        return None
    return total / weight


def _direction(score: float | None, hi: float = 6.5, lo: float = 3.5) -> Direction:
    if score is None:
        return "neutral"
    if score >= hi:
        return "supports"
    if score <= lo:
        return "contradicts"
    return "neutral"


def _split_factor(snapshot: InstrumentSnapshot, period_end: date) -> float:
    """Cumulative split factor applied after ``period_end`` (and on/before
    as_of), so per-share figures across a split stay comparable."""
    factor = 1.0
    for action in snapshot.corporate_actions:
        if (
            action.kind == "split"
            and action.factor
            and action.ex_date <= snapshot.as_of
            and period_end < action.ex_date
        ):
            factor *= float(action.factor)
    return factor


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


def analyse(snapshot: InstrumentSnapshot, settings: Settings) -> EngineResult:
    """Sector-aware fundamental quality (see module docstring for weights)."""
    qs = snapshot.quarterlies()
    if len(qs) < 4:
        return abstain(
            _ENGINE,
            f"insufficient fundamentals: {len(qs)} quarterly report(s) visible, need at least 4",
            data_quality=round(min(0.4, len(qs) * 0.1), 2),
        )

    sclass = sector_class(snapshot)
    latest = qs[-1]
    warnings: list[str] = []
    evidence: list[Evidence] = []

    def dref(formula: str, rec: FundamentalRecord | None = None) -> SourceRef:
        base = rec if rec is not None else latest
        return derived_ref(snapshot, formula, based_on=base.source)

    def add(
        key: str,
        pillar: Pillar,
        statement: str,
        value: float | str | None,
        direction: Direction,
        source: SourceRef | None = None,
    ) -> None:
        evidence.append(ev(snapshot, key, statement, value, direction, pillar, source or dref(key)))

    def _sl(value: float | None, worst: float, best: float) -> float | None:
        if value is None:
            return None
        return scale_linear(value, worst, best)

    # ---- point-in-time TTM series (oldest -> newest) ---------------------
    factors = [_split_factor(snapshot, q.period_end) for q in qs]
    rev = _ttm([q.revenue for q in qs])
    gp = _ttm([q.gross_profit for q in qs])
    op = _ttm([q.operating_income for q in qs])
    ni = _ttm([q.net_income for q in qs])
    ocf = _ttm([q.operating_cash_flow for q in qs])
    fcf = _ttm([q.free_cash_flow for q in qs])
    sbc = _ttm([q.stock_based_comp for q in qs])
    interest = _ttm([q.interest_expense for q in qs])
    dividends = _ttm(
        [abs(q.dividends_paid) if q.dividends_paid is not None else None for q in qs]
    )
    buybacks = _ttm([abs(q.buybacks) if q.buybacks is not None else None for q in qs])
    eps = _ttm(
        [
            (q.eps_diluted / f) if q.eps_diluted is not None else None
            for q, f in zip(qs, factors, strict=False)
        ]
    )
    shares = [
        (q.shares_diluted * f) if q.shares_diluted is not None else None
        for q, f in zip(qs, factors, strict=False)
    ]
    equity = [q.total_equity for q in qs]
    bvps = [_ratio(e, s) for e, s in zip(equity, shares, strict=False)]
    om_series = [_ratio(o, r) if r is not None and r > 0 else None for o, r in
                 zip(op, rev, strict=False)]
    gm_series = [_ratio(g, r) if r is not None and r > 0 else None for g, r in
                 zip(gp, rev, strict=False)]

    # ---- headline scalars -------------------------------------------------
    rev_now, rev_prev4, rev_prev8 = _at(rev), _at(rev, 4), _at(rev, 8)
    rev_yoy = _growth(rev_now, rev_prev4)
    rev_yoy_prior = _growth(rev_prev4, rev_prev8)
    rev_12 = _at(rev, 12)
    rev_cagr3 = (
        cagr(rev_12, rev_now, 3.0) if rev_12 is not None and rev_now is not None else None
    )
    eps_now, eps_prev4 = _at(eps), _at(eps, 4)
    eps_yoy: float | None = None
    eps_turned = False
    if eps_now is not None and eps_prev4 is not None:
        if eps_prev4 > 0:
            eps_yoy = eps_now / eps_prev4 - 1.0
        elif eps_now > 0:
            eps_turned = True
    eps_12 = _at(eps, 12)
    eps_cagr3 = (
        cagr(eps_12, eps_now, 3.0) if eps_12 is not None and eps_now is not None else None
    )
    fcf_now, fcf_prev4 = _at(fcf), _at(fcf, 4)
    fcf_yoy: float | None = None
    fcf_turned = False
    if fcf_now is not None and fcf_prev4 is not None:
        if fcf_prev4 > 0:
            fcf_yoy = fcf_now / fcf_prev4 - 1.0
        elif fcf_now > 0:
            fcf_turned = True
    fcf_12 = _at(fcf, 12)
    fcf_cagr3 = (
        cagr(fcf_12, fcf_now, 3.0) if fcf_12 is not None and fcf_now is not None else None
    )
    bvps_yoy = _growth(_at(bvps), _at(bvps, 4))

    rev_window = [v for v in rev[-9:] if v is not None]
    persistence = trend_consistency(rev_window)
    persistence_score: float | None = None
    if persistence is not None and len(rev_window) >= 3:
        if rev_window[-1] >= rev_window[0]:
            persistence_score = clamp(persistence * 10)
        else:  # a persistent decline is the opposite of quality growth
            persistence_score = clamp((1 - persistence) * 4)

    op_now, ni_now, ocf_now = _at(op), _at(ni), _at(ocf)
    interest_now = _at(interest)
    pretax = op_now - interest_now if op_now is not None and interest_now is not None else op_now
    tax_rate = _TAX_FALLBACK
    if pretax is not None and pretax > 0 and ni_now is not None and 0 < ni_now <= pretax:
        tax_rate = min(0.40, max(0.0, 1.0 - ni_now / pretax))
    nopat = op_now * (1.0 - tax_rate) if op_now is not None else None

    eq_now, debt_now, cash_now = latest.total_equity, latest.total_debt, latest.cash_and_equivalents
    invested = (
        eq_now + debt_now - cash_now
        if eq_now is not None and debt_now is not None and cash_now is not None
        else None
    )
    roic = _ratio(nopat, invested) if invested is not None and invested > 0 else None
    eq_prev = qs[-5].total_equity if len(qs) >= 5 else None
    eq_avg_vals = [v for v in (eq_now, eq_prev) if v is not None]
    eq_avg = fmean(eq_avg_vals) if eq_avg_vals else None
    roe = _ratio(ni_now, eq_avg) if eq_avg is not None and eq_avg > 0 else None

    roic_incr: float | None = None
    if len(qs) >= 9 and nopat is not None and invested is not None:
        q8 = qs[-9]
        invested_prev = (
            q8.total_equity + q8.total_debt - q8.cash_and_equivalents
            if q8.total_equity is not None
            and q8.total_debt is not None
            and q8.cash_and_equivalents is not None
            else None
        )
        op_prev8 = _at(op, 8)
        if op_prev8 is not None and invested_prev is not None:
            delta_ic = invested - invested_prev
            if delta_ic > 0:
                roic_incr = (nopat - op_prev8 * (1.0 - tax_rate)) / delta_ic

    om_now = _at(om_series)
    om_vals_3y = [v for v in om_series[-12:] if v is not None]
    om_avg3 = fmean(om_vals_3y) if len(om_vals_3y) >= 4 else None
    om_trend = om_now - om_avg3 if om_now is not None and om_avg3 is not None else None
    om_hist = [v for v in om_series if v is not None]
    om_mid = median(om_hist) if len(om_hist) >= 8 else None
    gm_now = _at(gm_series)
    fcf_margin = _ratio(fcf_now, rev_now) if rev_now is not None and rev_now > 0 else None

    net_debt = debt_now - cash_now if debt_now is not None and cash_now is not None else None
    nd_ebit = (
        net_debt / op_now
        if net_debt is not None and op_now is not None and op_now > 0
        else None
    )
    coverage = (
        op_now / interest_now
        if op_now is not None and interest_now is not None and interest_now > 0
        else None
    )
    current_ratio = (
        _ratio(latest.current_assets, latest.current_liabilities)
        if latest.current_liabilities is not None and latest.current_liabilities > 0
        else None
    )
    due_1y = latest.debt_due_within_1y
    refinancing_risk = bool(
        due_1y is not None
        and cash_now is not None
        and debt_now is not None
        and net_debt is not None
        and net_debt > 0
        and due_1y > cash_now
        and due_1y > 0.15 * debt_now
    )
    ocf_ni = ocf_now / ni_now if ni_now is not None and ni_now > 0 and ocf_now is not None else None

    shares_now, shares_prev4 = _at(shares), _at(shares, 4)
    dilution_pct: float | None = None
    if shares_now is not None and shares_prev4 is not None and shares_prev4 > 0:
        dilution_pct = (shares_now / shares_prev4 - 1.0) * 100.0
    sbc_ratio = _ratio(_at(sbc), rev_now) if rev_now is not None and rev_now > 0 else None
    div_now, bb_now = _at(dividends), _at(buybacks)
    payout_total = (
        (div_now or 0.0) + (bb_now or 0.0) if div_now is not None or bb_now is not None else None
    )

    buys = sells = 0
    last_insider_src: SourceRef | None = None
    for rec in snapshot.insiders:
        age = (snapshot.as_of - rec.transaction_date).days
        if 0 <= age <= _INSIDER_WINDOW_DAYS:
            if rec.kind == "buy":
                buys += 1
            else:
                sells += 1
            last_insider_src = rec.source
    cluster_buys = buys >= 2 and buys >= 2 * sells
    heavy_selling = sells >= 2 and sells >= 2 * buys

    # ---- accounting red flags --------------------------------------------
    red_flags: list[str] = []

    restatement_rec: FundamentalRecord | None = None
    for frec in snapshot.fundamentals:
        if (
            frec.is_restatement
            and (snapshot.as_of - frec.published_at.date()).days <= _RESTATEMENT_WINDOW_DAYS
            and (restatement_rec is None or frec.published_at > restatement_rec.published_at)
        ):
            restatement_rec = frec
    if restatement_rec is not None:
        text = f"restatement published {restatement_rec.published_at.date().isoformat()}"
        red_flags.append(text)
        add("red_flag_restatement", "quality", f"Red flag: {text} (within ~18 months)",
            None, "contradicts", restatement_rec.source)

    auditor_change: tuple[str, str] | None = None
    prev_auditor: str | None = None
    for q in qs:
        if q.auditor:
            if prev_auditor is not None and q.auditor != prev_auditor:
                auditor_change = (prev_auditor, q.auditor)
            prev_auditor = q.auditor
    if auditor_change is not None:
        text = f"auditor changed from {auditor_change[0]} to {auditor_change[1]}"
        red_flags.append(text)
        add("red_flag_auditor_change", "quality", f"Red flag: {text} between consecutive reports",
            None, "contradicts", latest.source)

    def _growth_pair(fieldname: str) -> tuple[float, float] | None:
        if len(qs) < 5:
            return None
        g_item = _growth(getattr(qs[-1], fieldname), getattr(qs[-5], fieldname))
        g_rev = _growth(qs[-1].revenue, qs[-5].revenue)
        if g_item is None or g_rev is None:
            return None
        return g_item, g_rev

    def _outpaces(pair: tuple[float, float] | None) -> bool:
        if pair is None:
            return False
        g_item, g_rev = pair
        return (g_item - g_rev) > 0.05 and (g_rev <= 0 or g_item > 1.5 * g_rev)

    recv_pair = _growth_pair("receivables") if sclass not in ("bank", "insurer") else None
    if _outpaces(recv_pair):
        assert recv_pair is not None
        text = (
            f"receivables grew {recv_pair[0] * 100:.0f}% vs revenue "
            f"{recv_pair[1] * 100:.0f}% over 4 quarters"
        )
        red_flags.append(text)
        add("red_flag_receivables", "quality", f"Red flag: {text}",
            round(recv_pair[0] * 100, 1), "contradicts", dref("receivables_vs_revenue_4q"))
    inv_pair = _growth_pair("inventory") if sclass not in ("bank", "insurer") else None
    if _outpaces(inv_pair):
        assert inv_pair is not None
        text = (
            f"inventory grew {inv_pair[0] * 100:.0f}% vs revenue "
            f"{inv_pair[1] * 100:.0f}% over 4 quarters"
        )
        red_flags.append(text)
        add("red_flag_inventory", "quality", f"Red flag: {text}",
            round(inv_pair[0] * 100, 1), "contradicts", dref("inventory_vs_revenue_4q"))

    excl_hits = 0
    for q in qs[-6:]:
        if (
            q.adjusted_profit_exclusions is not None
            and q.net_income is not None
            and abs(q.net_income) > 0
            and q.adjusted_profit_exclusions > 0.2 * abs(q.net_income)
        ):
            excl_hits += 1
    if excl_hits >= 3:
        text = (
            f"'adjusted' profit exclusions exceed 20% of net income in {excl_hits} "
            "of the last 6 quarters"
        )
        red_flags.append(text)
        add("red_flag_adjusted_exclusions", "quality", f"Red flag: {text}",
            excl_hits, "contradicts", latest.source)

    if sclass not in ("bank", "insurer") and ocf_ni is not None and ocf_ni < 0.7:
        text = f"weak cash conversion: OCF/NI is {ocf_ni:.2f} over the last 4 quarters"
        red_flags.append(text)
        add("red_flag_cash_conversion", "quality", f"Red flag: {text} (below 0.7)",
            round(ocf_ni, 2), "contradicts", dref("ocf_ni_4q"))

    # ---- components (sector branches) -------------------------------------
    quality_score: float | None
    growth_score: float | None
    balance_score: float | None
    cash_score: float | None
    shareholder_score: float | None

    if sclass in ("bank", "insurer"):
        nim = latest.sector_metrics.get("net_interest_margin")
        s_roe = _sl(roe, 0.04, 0.16)
        s_nim = _sl(nim, 0.015, 0.040)
        s_om = _sl(om_now, 0.25, 0.50)
        quality_score = _wmean([(0.40, s_roe), (0.30, s_nim), (0.30, s_om)])
        if roe is not None:
            add("roe_ttm", "quality", f"ROE (TTM) is {roe * 100:.1f}%",
                round(roe * 100, 1), _direction(s_roe))
        if nim is not None:
            add("net_interest_margin", "quality",
                f"Net interest margin is {nim * 100:.2f}%",
                round(nim * 100, 2), _direction(s_nim), latest.source)
        elif sclass == "bank":
            warnings.append("net interest margin unavailable — bank quality judged on ROE")

        tbv = [q.sector_metrics.get("tangible_book_per_share") for q in qs]
        tbv_yoy = _growth(_at(tbv), _at(tbv, 4))
        book_yoy = tbv_yoy if tbv_yoy is not None else bvps_yoy
        s_book = _sl(book_yoy, -0.02, 0.12)
        growth_score = _wmean([
            (0.40, _sl(rev_yoy, -0.08, 0.15)),
            (0.35, 8.0 if eps_turned else _sl(eps_yoy, -0.10, 0.25)),
            (0.25, s_book),
        ])
        if book_yoy is not None:
            label = "Tangible book value" if tbv_yoy is not None else "Book value"
            add("book_value_growth", "growth",
                f"{label} per share growth is {book_yoy * 100:.1f}% YoY",
                round(book_yoy * 100, 1), _direction(s_book))

        cet1 = latest.sector_metrics.get("cet1_ratio")
        b_parts: list[tuple[float, float | None]] = []
        if cet1 is not None:
            s_cet1 = _sl(cet1, 0.085, 0.145)
            b_parts.append((0.60, s_cet1))
            add("cet1_ratio", "balance_sheet", f"CET1 ratio is {cet1 * 100:.1f}%",
                round(cet1 * 100, 1), _direction(s_cet1), latest.source)
        else:
            cap_ratio = _ratio(eq_now, latest.total_assets)
            s_cap = _sl(cap_ratio, 0.04, 0.15)
            b_parts.append((0.60, s_cap))
            if cap_ratio is not None:
                add("equity_to_assets", "balance_sheet",
                    f"Equity/assets is {cap_ratio * 100:.1f}% (CET1 not disclosed)",
                    round(cap_ratio * 100, 1), _direction(s_cap))
            if sclass == "bank":
                warnings.append("CET1 unavailable — capital adequacy judged on equity/assets")
        prov = latest.sector_metrics.get("loan_loss_provisions")
        prov_ratio = _ratio(prov, latest.revenue)
        b_parts.append((0.40, _sl(prov_ratio, 0.10, 0.01)))
        balance_score = _wmean(b_parts)
        # Leverage semantics do not apply to lenders; capital adequacy replaces them.
        nd_ebit = None
        coverage = None
        refinancing_risk = False
        cash_score = None  # reported neutral, zero weight (accruals not meaningful)
    elif sclass == "reit":
        ffo = _ttm([q.sector_metrics.get("ffo") for q in qs])
        ffo_ps = _ttm([q.sector_metrics.get("ffo_per_share") for q in qs])
        ffo_now = _at(ffo)
        occupancy = latest.sector_metrics.get("occupancy")
        s_occ = _sl(occupancy, 0.85, 0.98)
        ffo_marg = _ratio(ffo_now, rev_now) if rev_now is not None and rev_now > 0 else None
        s_ffom = _sl(ffo_marg, 0.15, 0.55)
        quality_score = _wmean([(0.40, s_occ), (0.35, s_ffom), (0.25, _sl(om_now, 0.15, 0.40))])
        if occupancy is not None:
            add("occupancy", "quality", f"Occupancy is {occupancy * 100:.1f}%",
                round(occupancy * 100, 1), _direction(s_occ), latest.source)
        if ffo_marg is not None:
            add("ffo_margin", "quality", f"FFO margin (TTM) is {ffo_marg * 100:.1f}%",
                round(ffo_marg * 100, 1), _direction(s_ffom))
        elif ffo_now is None:
            warnings.append("FFO unavailable — REIT quality judged on occupancy and margins")

        ffo_ps_yoy = _growth(_at(ffo_ps), _at(ffo_ps, 4))
        s_ffog = _sl(ffo_ps_yoy, -0.05, 0.12)
        growth_score = _wmean([
            (0.50, s_ffog),
            (0.30, _sl(rev_yoy, -0.05, 0.10)),
            (0.20, persistence_score),
        ])
        if ffo_ps_yoy is not None:
            add("ffo_per_share_growth", "growth",
                f"FFO per share growth (TTM) is {ffo_ps_yoy * 100:.1f}% YoY",
                round(ffo_ps_yoy * 100, 1), _direction(s_ffog))

        ltv = latest.sector_metrics.get("ltv")
        b_parts = []
        if ltv is not None:
            s_ltv = _sl(ltv, 0.65, 0.25)
            b_parts.append((0.45, s_ltv))
            add("ltv", "balance_sheet", f"Loan-to-value is {ltv * 100:.0f}%",
                round(ltv * 100, 1), _direction(s_ltv), latest.source)
        else:
            warnings.append("LTV unavailable — REIT balance sheet judged on coverage")
        b_parts.append((0.25, _sl(coverage, 1.5, 6.0)))
        b_parts.append((0.10, _sl(current_ratio, 0.8, 2.0)))
        b_parts.append((0.20, 1.0 if refinancing_risk else 8.0))
        balance_score = _wmean(b_parts)
        cash_score = _generic_cash_quality(  # OCF/NI still meaningful for REITs
            add, _sl, ocf_ni, ni_now, ocf_now, qs, recv_pair, warnings
        )
        shareholder_base: float | None = ffo_now
    else:
        # general / commodity / early_stage share the industrial toolkit
        q_parts: list[tuple[float, float | None]] = []
        s_roic = _sl(roic, 0.02, 0.20)
        s_om_level = _sl(om_now, 0.0, 0.25)
        mid_ratio: float | None = None
        if sclass == "commodity":
            mid_ratio = _ratio(om_now, om_mid) if om_mid is not None and om_mid > 0 else None
            q_parts = [
                (0.35, s_roic),
                (0.15, _sl(roe, 0.02, 0.25)),
                (0.35, _sl(mid_ratio, 0.4, 1.2)),
                (0.15, _sl(fcf_margin, -0.05, 0.15)),
            ]
            if mid_ratio is not None:
                add("mid_cycle_margin", "quality",
                    f"Operating margin (TTM) is {mid_ratio:.2f}x its full-history median "
                    "(mid-cycle anchor)",
                    round(mid_ratio, 2), _direction(_sl(mid_ratio, 0.4, 1.2)))
                if mid_ratio > 1.5:
                    warnings.append("margins well above own full-history median — peak-margin risk")
                    add("peak_margin_risk", "quality",
                        "Margins are cyclically extended vs their own history — "
                        "peak-margin risk, not extrapolated",
                        round(mid_ratio, 2), "contradicts")
            elif om_now is None:
                warnings.append("operating margin history unavailable for cyclical normalisation")
        elif sclass == "early_stage":
            om_prev4 = _at(om_series, 4)
            om_delta = om_now - om_prev4 if om_now is not None and om_prev4 is not None else None
            q_parts = [(0.5, _sl(gm_now, 0.10, 0.60)), (0.5, _sl(om_delta, -0.05, 0.08))]
        else:
            q_parts = [
                (0.35, s_roic),
                (0.15, _sl(roe, 0.02, 0.25)),
                (0.20, s_om_level),
                (0.10, _sl(gm_now, 0.15, 0.60)),
                (0.20, _sl(om_trend, -0.05, 0.05)),
            ]
        quality_score = _wmean(q_parts)
        if quality_score is not None and roic_incr is not None and roic is not None:
            if roic_incr > roic + 0.02:
                quality_score = clamp(quality_score + 0.5)
                add("incremental_roic", "quality",
                    f"Incremental ROIC (2y) is {roic_incr * 100:.1f}%, above the "
                    f"{roic * 100:.1f}% base — returns improving on new capital",
                    round(roic_incr * 100, 1), "supports")
            elif roic_incr < roic * 0.5:
                quality_score = clamp(quality_score - 0.5)
                add("incremental_roic", "quality",
                    f"Incremental ROIC (2y) is {roic_incr * 100:.1f}%, well below the "
                    f"{roic * 100:.1f}% base — new capital earns less",
                    round(roic_incr * 100, 1), "contradicts")
        if sclass == "commodity" and quality_score is not None and (
            mid_ratio is not None and mid_ratio > 1.5
        ):
            quality_score = min(quality_score, 6.5)
        if roic is not None and sclass != "early_stage":
            add("roic_ttm", "quality",
                f"ROIC (TTM, NOPAT/(equity+debt-cash)) is {roic * 100:.1f}%",
                round(roic * 100, 1), _direction(s_roic))
        if om_now is not None:
            stmt = f"Operating margin (TTM) is {om_now * 100:.1f}%"
            if om_avg3 is not None:
                stmt += f" vs {om_avg3 * 100:.1f}% 3-year average"
            add("operating_margin_ttm", "quality", stmt, round(om_now * 100, 1),
                _direction(_sl(om_trend, -0.05, 0.05) if om_trend is not None else s_om_level))

        if sclass == "early_stage":
            growth_score = _wmean([
                (0.70, _sl(rev_yoy, -0.05, 0.40)),
                (0.30, persistence_score),
            ])
        else:
            growth_score = _wmean([
                (0.35, _sl(rev_yoy, -0.10, 0.25)),
                (0.25, 8.0 if eps_turned else _sl(eps_yoy, -0.15, 0.35)),
                (0.15, 8.0 if fcf_turned else _sl(fcf_yoy, -0.15, 0.35)),
                (0.15, persistence_score),
                (0.10, _sl(bvps_yoy, -0.05, 0.20)),
            ])
        if growth_score is not None and rev_yoy is not None and rev_yoy_prior is not None:
            if rev_yoy > rev_yoy_prior + 0.02:
                growth_score = clamp(growth_score + 0.75)
            elif rev_yoy < rev_yoy_prior - 0.05:
                growth_score = clamp(growth_score - 0.75)

        if sclass == "early_stage":
            runway_years: float | None = None
            s_runway: float | None
            if fcf_now is not None and fcf_now < 0 and cash_now is not None:
                runway_years = cash_now / (-fcf_now)
                s_runway = _sl(runway_years, 0.4, 3.0)
                add("cash_runway", "balance_sheet",
                    f"Cash runway is {runway_years:.1f} years at the current FCF burn rate",
                    round(runway_years, 1), _direction(s_runway))
            elif fcf_now is not None and fcf_now >= 0:
                s_runway = 7.5
                add("cash_runway", "balance_sheet",
                    "TTM free cash flow is non-negative — not reliant on external funding",
                    None, "supports")
            else:
                s_runway = None
                warnings.append("cash or FCF unavailable — runway not computable")
            balance_score = _wmean([(0.80, s_runway), (0.20, _sl(current_ratio, 0.8, 2.0))])
        else:
            s_lev: float | None
            if net_debt is not None and net_debt <= 0:
                s_lev = 9.5
                add("net_debt_ebit", "balance_sheet",
                    "Net cash position: total debt is below cash on hand",
                    round(nd_ebit, 2) if nd_ebit is not None else None, "supports")
            elif nd_ebit is not None:
                s_lev = _sl(nd_ebit, 4.0, 0.0)
                add("net_debt_ebit", "balance_sheet",
                    f"Net debt is {nd_ebit:.1f}x TTM EBIT (EBITDA not disclosed; EBIT used)",
                    round(nd_ebit, 2), _direction(s_lev))
            elif net_debt is not None and op_now is not None and op_now <= 0:
                s_lev = 1.0
                warnings.append("TTM EBIT non-positive with net debt — leverage ratio not computable")
            else:
                s_lev = None
                warnings.append("leverage unassessed — debt, cash or TTM EBIT unavailable")
            s_cov = _sl(coverage, 2.0, 12.0)
            if coverage is not None:
                add("interest_coverage", "balance_sheet",
                    f"Interest coverage (TTM EBIT/interest) is {coverage:.1f}x",
                    round(coverage, 1), _direction(s_cov))
            balance_score = _wmean([
                (0.40, s_lev),
                (0.30, s_cov),
                (0.15, _sl(current_ratio, 0.8, 2.0)),
                (0.15, 1.0 if refinancing_risk else 8.0),
            ])
        cash_score = _generic_cash_quality(
            add, _sl, ocf_ni, ni_now, ocf_now, qs, recv_pair, warnings
        )
        shareholder_base = fcf_now

    if sclass in ("bank", "insurer"):
        shareholder_base = ni_now
        base_label = "net income"
    elif sclass == "reit":
        base_label = "FFO"
    else:
        base_label = "FCF"

    if refinancing_risk and due_1y is not None and cash_now is not None:
        add("refinancing_wall", "balance_sheet",
            f"Debt due within 1 year is {due_1y / 1e6:,.0f}m vs {cash_now / 1e6:,.0f}m cash — "
            "refinancing wall",
            round(due_1y / cash_now, 1) if cash_now > 0 else None, "contradicts")

    # ---- shareholder alignment (common) -----------------------------------
    sh_parts: list[tuple[float, float | None]] = []
    s_dil = _sl(dilution_pct, 5.0, -3.0)
    sh_parts.append((0.30, s_dil))
    sh_parts.append((0.20, _sl(sbc_ratio, 0.12, 0.0)))
    payout_ratio: float | None = None
    s_payout: float | None = None
    if payout_total is not None and payout_total <= 0:
        s_payout = 6.5
    elif payout_total is not None and shareholder_base is not None:
        if shareholder_base > 0:
            payout_ratio = payout_total / shareholder_base
            s_payout = _sl(payout_ratio, 1.4, 0.4)
            add("payout_vs_cash", "quality",
                f"Dividends + buybacks are {payout_ratio * 100:.0f}% of TTM {base_label}",
                round(payout_ratio * 100, 1), _direction(s_payout))
        else:
            s_payout = 1.0
            add("payout_unfunded", "quality",
                f"Dividends/buybacks are being paid while TTM {base_label} is negative — "
                "payout looks unsustainable",
                None, "contradicts")
    sh_parts.append((0.30, s_payout))
    if snapshot.insiders:
        s_ins = 5.0
        if cluster_buys:
            s_ins = 8.5
            add("insider_cluster_buys", "quality",
                f"Insider cluster buying: {buys} buy(s) vs {sells} sell(s) in the last 180 days",
                buys, "supports", last_insider_src)
        elif heavy_selling:
            s_ins = 1.5
            add("insider_heavy_selling", "quality",
                f"Heavy insider selling: {sells} sell(s) vs {buys} buy(s) in the last 180 days",
                sells, "contradicts", last_insider_src)
        sh_parts.append((0.20, s_ins))
    shareholder_score = _wmean(sh_parts)
    if dilution_pct is not None:
        add("dilution_1y", "quality",
            f"Diluted share count changed {dilution_pct:+.1f}% over the last four quarters",
            round(dilution_pct, 2), _direction(s_dil))

    # ---- growth evidence (common) ------------------------------------------
    if rev_yoy is not None:
        stmt = f"TTM revenue growth is {rev_yoy * 100:.1f}% YoY"
        if rev_cagr3 is not None:
            stmt += f" ({rev_cagr3 * 100:.1f}% 3y CAGR)"
        add("revenue_growth_ttm", "growth", stmt, round(rev_yoy * 100, 1),
            _direction(_sl(rev_yoy, -0.10, 0.25)))
    elif rev_now is None:
        warnings.append("revenue not reported — growth largely unassessable")
    if eps_turned:
        add("eps_inflection", "growth", "TTM EPS turned positive versus a year ago",
            None, "supports")
    elif eps_yoy is not None and sclass != "early_stage":
        add("eps_growth_ttm", "growth", f"TTM EPS growth is {eps_yoy * 100:.1f}% YoY",
            round(eps_yoy * 100, 1), _direction(_sl(eps_yoy, -0.15, 0.35)))

    # ---- customer concentration --------------------------------------------
    conc = latest.largest_customer_pct
    if conc is not None and conc > _CONCENTRATION_PCT and quality_score is not None:
        quality_score = clamp(quality_score - min(2.0, (conc - _CONCENTRATION_PCT) / 15.0))
        add("customer_concentration", "quality",
            f"Largest customer is {conc:.0f}% of revenue — concentration risk",
            round(conc, 1), "contradicts", latest.source)

    # ---- blend --------------------------------------------------------------
    comp_raw: dict[str, float | None] = {
        "quality": quality_score,
        "growth": growth_score,
        "balance_sheet": balance_score,
        "cash_quality": cash_score,
        "shareholder": shareholder_score,
    }
    weights = _WEIGHTS[sclass]
    score = _wmean([(weights[name], comp_raw[name]) for name in comp_raw])
    if score is None:
        return abstain(
            _ENGINE,
            "fundamental records lack the fields needed to assess quality",
            data_quality=0.2,
        )
    uncomputed = [k for k, v in comp_raw.items() if v is None and weights[k] > 0]
    if uncomputed:
        warnings.append(
            "components not computable from available fields (shown neutral, zero weight): "
            + ", ".join(sorted(uncomputed))
        )

    if red_flags:
        score -= min(_RED_FLAG_PENALTY_CAP, _RED_FLAG_PENALTY * len(red_flags))
        warnings.append(f"{len(red_flags)} accounting red flag(s) — see details.red_flags")
    if sclass == "early_stage":
        score = min(score, _EARLY_STAGE_CAP)
        warnings.append("early-stage/pre-profit: score capped at 6.0 — quality unprovable pre-profit")

    # ---- details --------------------------------------------------------------
    rev_2y_ago = _at(rev, 8)
    structural_decline = bool(
        rev_now is not None
        and rev_2y_ago is not None
        and rev_now < rev_2y_ago
        and (rev_cagr3 is None or rev_cagr3 < 0)
    )
    margin_collapse = bool(
        om_now is not None and om_avg3 is not None and om_avg3 > 0 and om_now < 0.6 * om_avg3
    )
    # Leverage is judged in each sector's own terms: LTV for REITs, capital
    # adequacy for lenders, net debt / TTM EBIT for everyone else.
    if sclass in ("bank", "insurer"):
        cet1_now = latest.sector_metrics.get("cet1_ratio")
        excess_leverage = bool(cet1_now is not None and cet1_now < 0.09)
    elif sclass == "reit":
        ltv_now = latest.sector_metrics.get("ltv")
        excess_leverage = bool(
            (ltv_now is not None and ltv_now > 0.55) or refinancing_risk
        )
    else:
        excess_leverage = bool((nd_ebit is not None and nd_ebit > 4.0) or refinancing_risk)
    details = {
        "sector_class": sclass,
        "red_flags": red_flags,
        "value_trap_inputs": {
            "structural_revenue_decline": structural_decline,
            "margin_collapse": margin_collapse,
            "excess_leverage": excess_leverage,
            "dilution": bool(dilution_pct is not None and dilution_pct > 3.0),
            "weak_cash_conversion": bool(ocf_ni is not None and ocf_ni < 0.7),
            "governance_flags": bool(
                restatement_rec is not None or auditor_change is not None or excl_hits >= 3
            ),
        },
        "growth_metrics": {
            "revenue_cagr_3y": round(rev_cagr3, 4) if rev_cagr3 is not None else None,
            "eps_cagr_3y": round(eps_cagr3, 4) if eps_cagr3 is not None else None,
            "fcf_cagr_3y": round(fcf_cagr3, 4) if fcf_cagr3 is not None else None,
        },
        "net_debt_ebitda": round(nd_ebit, 2) if nd_ebit is not None else None,
        "interest_coverage": round(coverage, 2) if coverage is not None else None,
        "refinancing_risk": refinancing_risk,
        "dilution_pct_1y": round(dilution_pct, 2) if dilution_pct is not None else None,
    }

    # ---- data quality -----------------------------------------------------------
    needed: tuple[str, ...]
    if sclass in ("bank", "insurer"):
        needed = ("revenue", "operating_income", "net_income", "total_equity",
                  "total_assets", "shares_diluted")
    elif sclass == "reit":
        needed = ("revenue", "operating_income", "net_income", "operating_cash_flow",
                  "total_debt", "cash_and_equivalents", "shares_diluted", "dividends_paid")
    else:
        needed = ("revenue", "gross_profit", "operating_income", "net_income",
                  "operating_cash_flow", "capex", "total_equity", "total_debt",
                  "cash_and_equivalents", "shares_diluted")
    window4 = qs[-4:]
    present = fmean(
        [1.0 if getattr(q, f) is not None else 0.0 for q in window4 for f in needed]
    )
    depth = min(1.0, len(qs) / 12.0)
    age_days = (snapshot.as_of - latest.published_at.date()).days
    fresh = 1.0 if age_days <= 120 else (0.7 if age_days <= 240 else 0.4)
    if age_days > 200:
        warnings.append(f"latest fundamentals are {age_days} days old")
    dq = 0.5 * present + 0.3 * depth + 0.2 * fresh
    if sclass == "bank" and latest.sector_metrics.get("cet1_ratio") is None:
        dq *= 0.8
    if sclass == "reit" and (
        latest.sector_metrics.get("ffo") is None or latest.sector_metrics.get("ltv") is None
    ):
        dq *= 0.8

    return EngineResult(
        engine=_ENGINE,
        score=round(clamp(score), 2),
        components={
            name: round(value if value is not None else 5.0, 2)
            for name, value in comp_raw.items()
        },
        evidence=evidence,
        warnings=warnings,
        data_quality=round(min(1.0, dq), 2),
        details=details,
    )


def _generic_cash_quality(
    add: Callable[..., None],
    _sl: Callable[[float | None, float, float], float | None],
    ocf_ni: float | None,
    ni_now: float | None,
    ocf_now: float | None,
    qs: list[FundamentalRecord],
    recv_pair: tuple[float, float] | None,
    warnings: list[str],
) -> float | None:
    """Cash quality for non-lenders: OCF/NI, accruals, receivables discipline."""
    parts: list[tuple[float, float | None]] = []
    if ocf_ni is not None:
        s_ocf = _sl(ocf_ni, 0.5, 1.25)
        parts.append((0.45, s_ocf))
        add("ocf_ni_4q", "quality", f"Cash conversion (OCF/NI, 4 quarters) is {ocf_ni:.2f}",
            round(ocf_ni, 2), _direction(s_ocf))
    elif ni_now is not None and ni_now <= 0 and ocf_now is not None:
        parts.append((0.45, 6.0 if ocf_now > 0 else 2.5))
        warnings.append("net income non-positive — cash conversion judged on OCF sign")
    assets_now = qs[-1].total_assets
    assets_prev = qs[-5].total_assets if len(qs) >= 5 else None
    asset_vals = [v for v in (assets_now, assets_prev) if v is not None]
    assets_avg = fmean(asset_vals) if asset_vals else None
    accruals = (
        (ni_now - ocf_now) / assets_avg
        if ni_now is not None and ocf_now is not None and assets_avg is not None and assets_avg > 0
        else None
    )
    if accruals is not None:
        parts.append((0.35, _sl(accruals, 0.12, -0.04)))
        if accruals > 0.08:
            add("accruals_ratio", "quality",
                f"Accruals ((NI-OCF)/assets, TTM) are {accruals * 100:.1f}% — "
                "earnings run ahead of cash",
                round(accruals * 100, 1), "contradicts")
    if recv_pair is not None:
        parts.append((0.20, _sl(recv_pair[0] - recv_pair[1], 0.15, -0.05)))
    return _wmean(parts)
