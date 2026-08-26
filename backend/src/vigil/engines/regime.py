"""Market-regime classification and instrument risk profiling.

The regime engine answers two questions: (a) what kind of market
environment is this instrument trading in, and (b) how risky is this
particular instrument right now. The engine ``score`` is the environment
favourability for THIS instrument: 10 means a benign market regime and a
low-risk, liquid instrument, 5 is neutral, 0 is a hostile regime and/or a
very risky instrument.

Score construction (plain English)
----------------------------------
Three components, each 0-10, are blended with fixed weights. When a
component cannot be computed from the available data its weight is dropped
and the remaining weights renormalise (a warning is added; the components
dict then shows a neutral 5.0 for that entry):

* ``regime`` (45%) — the market environment. The benchmark series is
  classified with ordered deterministic rules (first match wins), using
  the benchmark's 200-day average and its slope over the last 40 sessions,
  the 50-day average and its 20-session slope, the drawdown from the
  52-week high (dd), the 3-month benchmark return, and macro conditions
  (volatility gauge and credit spreads, each compared against its own
  one-year median so the rules self-calibrate to the data source):

  1. ``recovery`` — dd <= -12% but the 3-month return is >= +10% with the
     benchmark above a rising 50-day average;
  2. ``stress``   — dd <= -40%, or dd <= -18% while the volatility gauge is
     extreme (>= 35, or >= 1.6x its one-year median and >= 28) or credit
     spreads are stressed (>= 1.75x their one-year median, or >= 450bps);
  3. ``bear``     — benchmark below a falling 200-day average and dd <= -15%;
  4. ``correction`` — dd <= -8%;
  5. ``bull``     — benchmark above its 200-day average with a flat-or-rising
     slope;
  6. ``choppy``   — anything else (mixed signals).

  The component starts from a per-label base (bull 8.0, recovery 6.5,
  choppy 5.0, correction 4.0, bear 2.5, stress 1.0) and is then adjusted:
  -1.0 when the volatility gauge is extreme, else -0.5 when elevated
  (>= 1.25x its one-year median and >= 20); -1.0 when credit spreads are
  stressed, else -0.5 when elevated (>= 1.3x their median, or >= 300bps)
  AND rising (>= +15% vs ~90 days earlier); +/-0.5 for sector breadth (the
  sector index out/under-performing the market by 5pp over 3 months).
  Fewer than 200 benchmark bars means classification is not attempted: the
  label falls back to ``choppy`` with zero adjustment, the component's
  weight is dropped, and a warning is added.

  ``details.regime_adjustment`` is a fixed per-label tilt consumed by
  composite scoring (short/medium horizons only): bull +0.25,
  recovery +0.10, choppy 0.00, correction -0.25, bear -0.50, stress -0.75.

* ``instrument_risk`` (35%) — inverted, 10 = low risk. Base = weighted
  mean of the computable metric scores (weights renormalise):
  1-year realised volatility (30%; 15% annualised -> 10, 60% -> 0), the
  worse of beta and downside beta vs the benchmark over 2 years of daily
  returns (25%; 0.6 -> 10, 2.0 -> 0), maximum drawdown over 2 years (25%;
  -10% -> 10, -60% -> 0), and gap risk (20%; the share of the last year's
  sessions with a |move| > 5%: 0% -> 10, 6% -> 0). Flag deductions are
  then subtracted (each adds a reason to ``details.risk_factors`` and
  contradicting evidence): high leverage -1.5 / moderate -0.75
  (sector-aware bands: net debt / TTM operating profit > 5x high, > 3x
  moderate for general names; loan books are exempt and banks are flagged
  only on CET1 < 11%; REITs use debt/assets LTV > 60% high, > 45%
  moderate), an unresolved binary catalyst within 30 calendar days -1.0,
  rate sensitivity -0.75 (REIT or high-leverage name while the policy rate
  rose >= 0.25pp over ~6 months), FX exposure -0.25 (reporting currency
  differs from the GBP base), and momentum-crash vulnerability -1.0
  (12-month return > +60%, short interest > 10% of float, and the regime
  is not bull). Clamped to [0, 10].

* ``liquidity_risk`` (20%) — 10 = liquid. The median daily traded value
  (base currency) is divided by the universe liquidity floor: a ratio
  >= 8 is a ``high`` band, >= 2.5 ``medium``, >= 1 ``low``, else
  ``very_low``; an estimated spread >= 60bps downgrades the band one step.
  Band scores: high 9.0, medium 6.5, low 4.0, very_low 1.5. When the
  traded value is unknown the band is conservatively reported as ``low``
  for downstream gating but the component is dropped from the blend with a
  warning (never scored on invented data).

``details.risk_score`` (0-10, 10 = extremely risky) = 10 minus the
instrument_risk component (a neutral 5.0 starting point with a warning
when that component was not computable), +1.0 in a stress regime, +0.5 in
a bear regime, +0.5 when liquidity is very_low; clamped to [0, 10].

Abstains when the benchmark series is empty (the environment cannot be
classified), and — never fabricating a score — in the degenerate case
where no component is computable at all.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from vigil.config import Settings
from vigil.engines.base import abstain, derived_ref, ev, price_ref, sector_class
from vigil.indicators import stats, ta
from vigil.schemas.core import (
    Direction,
    EngineResult,
    Evidence,
    InstrumentSnapshot,
    SourceRef,
)

_ENGINE = "regime"

_WEIGHTS: dict[str, float] = {
    "regime": 0.45,
    "instrument_risk": 0.35,
    "liquidity_risk": 0.20,
}

_ENV_BASE: dict[str, float] = {
    "bull": 8.0, "recovery": 6.5, "choppy": 5.0,
    "correction": 4.0, "bear": 2.5, "stress": 1.0,
}
_ADJUSTMENT: dict[str, float] = {
    "bull": 0.25, "recovery": 0.10, "choppy": 0.0,
    "correction": -0.25, "bear": -0.50, "stress": -0.75,
}

_BETA_WINDOW = 504          # ~2y of daily returns
_MDD_LOOKBACK = 504
_VOL_WINDOW = 252           # 1y realised volatility
_GAP_WINDOW = 252
_GAP_THRESHOLD = 0.05
_MIN_GAP_BARS = 60
_BINARY_WINDOW_DAYS = 30
_RATE_LOOKBACK_DAYS = 183   # ~6 months
_RATE_RISE_PP = 0.25
_CRASH_RETURN_12M = 0.60
_CRASH_SHORT_PCT = 10.0
_SPREAD_DOWNGRADE_BPS = 60.0
_MACRO_MEDIAN_DAYS = 365

_BAND_ORDER = ("very_low", "low", "medium", "high")
_BAND_SCORE: dict[str, float] = {"high": 9.0, "medium": 6.5, "low": 4.0, "very_low": 1.5}

_LEVERAGE_DEDUCTION: dict[str, float] = {"high": 1.5, "moderate": 0.75}
_BINARY_DEDUCTION = 1.0
_RATE_DEDUCTION = 0.75
_FX_DEDUCTION = 0.25
_CRASH_DEDUCTION = 1.0


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


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


def _sl(value: float | None, worst: float, best: float) -> float | None:
    if value is None:
        return None
    return stats.scale_linear(value, worst, best)


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def _macro_pick(
    macro: dict[str, pd.Series], candidates: tuple[str, ...], contains: str
) -> tuple[str, pd.Series] | None:
    """First matching macro series by exact name, then by substring."""
    for name in candidates:
        series = macro.get(name)
        if series is not None and not series.dropna().empty:
            return name, series.dropna().sort_index()
    for name in sorted(macro):
        if contains in name:
            series = macro[name].dropna()
            if not series.empty:
                return name, series.sort_index()
    return None


def _macro_ref(snapshot: InstrumentSnapshot, name: str, series: pd.Series) -> SourceRef:
    last = series.index[-1]
    published = last.to_pydatetime()
    return SourceRef(
        provider="macro-data",
        source_type="macro",
        reference=f"series:{name}",
        published_at=published,
        freshness_days=float((snapshot.as_of - published.date()).days),
    )


def _asof_value(series: pd.Series, cutoff: date) -> float | None:
    upto = series.loc[: pd.Timestamp(cutoff)]
    if upto.empty:
        return None
    return float(upto.iloc[-1])


def _recent_median(series: pd.Series, as_of: date, days: int = _MACRO_MEDIAN_DAYS) -> float:
    window = series.loc[series.index >= pd.Timestamp(as_of - timedelta(days=days))]
    if len(window) < 8:
        window = series
    return float(window.median())


def _classify(
    *,
    above_200: bool | None,
    slope_200: float | None,
    dd: float | None,
    ret_3m: float | None,
    above_50: bool | None,
    slope_50: float | None,
    vix_extreme: bool,
    spread_stressed: bool,
) -> str:
    """Ordered deterministic regime rules — see the module docstring."""
    if (
        dd is not None and dd <= -0.12
        and ret_3m is not None and ret_3m >= 0.10
        and above_50 is True and slope_50 is not None and slope_50 > 0
    ):
        return "recovery"
    if dd is not None and (dd <= -0.40 or (dd <= -0.18 and (vix_extreme or spread_stressed))):
        return "stress"
    if (
        above_200 is False and slope_200 is not None and slope_200 < 0
        and dd is not None and dd <= -0.15
    ):
        return "bear"
    if dd is not None and dd <= -0.08:
        return "correction"
    if above_200 is True and (slope_200 is None or slope_200 >= 0):
        return "bull"
    return "choppy"


def _leverage_band(
    snapshot: InstrumentSnapshot, sclass: str
) -> tuple[str | None, str | None, float | None]:
    """(band, human reason, headline ratio). band None = not assessable.

    Bands: general/commodity/early_stage use net debt / TTM operating
    profit (>5x high, >3x moderate, net cash reported as such); banks and
    insurers are flagged only on thin capital (CET1 < 11%); REITs use
    debt/assets LTV (>60% high, >45% moderate).
    """
    latest = snapshot.latest_fundamental()
    if latest is None:
        return None, None, None
    if sclass in ("bank", "insurer"):
        cet1 = latest.sector_metrics.get("cet1_ratio")
        if cet1 is None:
            return None, None, None
        if cet1 < 11.0:
            return "high", f"thin capital adequacy (CET1 {cet1:.1f}%)", cet1
        return "low", None, cet1
    if sclass == "reit":
        if latest.total_debt is None or latest.total_assets in (None, 0):
            return None, None, None
        ltv = latest.total_debt / float(latest.total_assets)
        if ltv > 0.60:
            return "high", f"high leverage (LTV {ltv * 100:.0f}% of assets)", ltv
        if ltv > 0.45:
            return "moderate", f"moderate leverage (LTV {ltv * 100:.0f}% of assets)", ltv
        return "low", None, ltv
    if latest.total_debt is None or latest.cash_and_equivalents is None:
        return None, None, None
    net_debt = latest.total_debt - latest.cash_and_equivalents
    if net_debt <= 0:
        return "net_cash", "net cash balance sheet", 0.0
    ttm_op = snapshot.ttm_sum("operating_income")
    if ttm_op is None:
        return None, None, None
    if ttm_op <= 0:
        return "high", "net debt with no TTM operating profit", None
    ratio = net_debt / ttm_op
    if ratio > 5.0:
        return "high", f"high leverage (net debt {ratio:.1f}x TTM operating profit)", ratio
    if ratio > 3.0:
        return "moderate", f"moderate leverage (net debt {ratio:.1f}x TTM operating profit)", ratio
    return "low", None, ratio


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


def analyse(snapshot: InstrumentSnapshot, settings: Settings) -> EngineResult:
    """Regime classification + instrument risk (see module docstring)."""
    bench = snapshot.benchmark.dropna() if snapshot.benchmark is not None else pd.Series(dtype=float)
    if bench.empty:
        return abstain(
            _ENGINE, "benchmark series is empty — market regime cannot be classified", 0.0
        )

    warnings: list[str] = []
    evidence: list[Evidence] = []
    pref = price_ref(snapshot)

    def add(
        key: str,
        statement: str,
        value: float | str | None,
        direction: Direction,
        source: SourceRef,
    ) -> None:
        evidence.append(ev(snapshot, key, statement, value, direction, "risk", source))

    def dref(formula: str, based_on: SourceRef | None = None) -> SourceRef:
        return derived_ref(snapshot, formula, based_on=based_on or pref)

    # ---- macro conditions ---------------------------------------------------
    market = snapshot.info.market.upper()
    rate_names = (
        ("uk_policy_rate", "policy_rate", "rates", "uk_10y_yield")
        if market == "UK"
        else ("us_policy_rate", "policy_rate", "rates", "us_10y_yield")
    )
    vix_pick = _macro_pick(snapshot.macro, ("vix",), "vix")
    spread_pick = _macro_pick(
        snapshot.macro,
        ("credit_spread", "us_credit_spread_bps", "uk_credit_spread_bps", "credit_spread_bps"),
        "credit_spread",
    )
    rate_pick = _macro_pick(snapshot.macro, rate_names, "policy_rate")

    vix_last: float | None = None
    vix_median: float | None = None
    vix_elevated = vix_extreme = False
    if vix_pick is not None:
        _, vix_series = vix_pick
        vix_last = float(vix_series.iloc[-1])
        vix_median = _recent_median(vix_series, snapshot.as_of)
        vix_elevated = vix_last >= max(20.0, 1.25 * vix_median)
        vix_extreme = vix_last >= 35.0 or vix_last >= max(28.0, 1.6 * vix_median)
    else:
        warnings.append("no volatility-index macro series — volatility conditions not assessed")

    spread_last: float | None = None
    spread_median: float | None = None
    spread_elevated = spread_stressed = spread_rising = False
    if spread_pick is not None:
        _, spread_series = spread_pick
        if float(spread_series.median()) < 25.0:  # quoted in percent, not bps
            spread_series = spread_series * 100.0
        spread_last = float(spread_series.iloc[-1])
        spread_median = _recent_median(spread_series, snapshot.as_of)
        prev = _asof_value(spread_series, snapshot.as_of - timedelta(days=90))
        spread_elevated = spread_last >= 1.3 * spread_median or spread_last >= 300.0
        spread_stressed = spread_last >= 1.75 * spread_median or spread_last >= 450.0
        spread_rising = prev is not None and prev > 0 and spread_last >= 1.15 * prev
    else:
        warnings.append("no credit-spread macro series — credit conditions not assessed")

    rate_rising = False
    rate_now: float | None = None
    if rate_pick is not None:
        _, rate_series = rate_pick
        rate_now = float(rate_series.iloc[-1])
        rate_prev = _asof_value(
            rate_series, snapshot.as_of - timedelta(days=_RATE_LOOKBACK_DAYS)
        )
        rate_rising = rate_prev is not None and (rate_now - rate_prev) >= _RATE_RISE_PP
    else:
        warnings.append("no policy-rate macro series — rate sensitivity not assessed")

    # ---- benchmark trend and regime classification ---------------------------
    bench_last = float(bench.iloc[-1])
    sma200 = ta.sma(bench, 200).dropna()
    sma50 = ta.sma(bench, 50).dropna()
    above_200 = bench_last > float(sma200.iloc[-1]) if not sma200.empty else None
    above_50 = bench_last > float(sma50.iloc[-1]) if not sma50.empty else None
    slope_200 = ta.slope(sma200, 40) if not sma200.empty else None
    slope_50 = ta.slope(sma50, 20) if not sma50.empty else None
    dd = ta.drawdown_from_high(bench, 252)
    ret_3m = ta.momentum(bench, 63)

    classified = above_200 is not None
    if classified:
        label = _classify(
            above_200=above_200, slope_200=slope_200, dd=dd, ret_3m=ret_3m,
            above_50=above_50, slope_50=slope_50,
            vix_extreme=vix_extreme, spread_stressed=spread_stressed,
        )
        env_score: float | None = _ENV_BASE[label]
    else:
        label = "choppy"
        env_score = None
        warnings.append(
            f"benchmark history too short to classify the market regime "
            f"({len(bench)} bars, need 200) — no regime tilt applied"
        )
    regime_adjustment = _ADJUSTMENT[label] if classified else 0.0

    breadth = (
        ta.relative_strength(snapshot.sector_index.dropna(), bench, 63)
        if snapshot.sector_index is not None and not snapshot.sector_index.dropna().empty
        else None
    )
    if env_score is not None:
        if vix_extreme:
            env_score -= 1.0
        elif vix_elevated:
            env_score -= 0.5
        if spread_stressed:
            env_score -= 1.0
        elif spread_elevated and spread_rising:
            env_score -= 0.5
        if breadth is not None:
            if breadth >= 0.05:
                env_score += 0.5
            elif breadth <= -0.05:
                env_score -= 0.5
        env_score = stats.clamp(env_score)

    if classified and dd is not None:
        side = "above" if above_200 else "below"
        add(
            "regime_classification",
            f"Market regime is '{label}': benchmark {side} its 200-day average and "
            f"{dd * 100:+.1f}% from its 52-week high",
            label,
            "supports" if label in ("bull", "recovery")
            else ("neutral" if label == "choppy" else "contradicts"),
            dref("regime_classification"),
        )
    if vix_last is not None and vix_median is not None and vix_pick is not None:
        add(
            "vix_level",
            f"Volatility gauge ({vix_pick[0]}) at {vix_last:.1f} vs "
            f"{vix_median:.1f} one-year median",
            round(vix_last, 2),
            "contradicts" if (vix_elevated or vix_extreme)
            else ("supports" if vix_last <= 0.9 * vix_median else "neutral"),
            _macro_ref(snapshot, vix_pick[0], vix_pick[1]),
        )
    if spread_last is not None and spread_median is not None and spread_pick is not None:
        trend_txt = " and rising" if spread_rising else ""
        add(
            "credit_spread",
            f"Credit spreads at {spread_last:.0f}bps vs {spread_median:.0f}bps "
            f"one-year median{trend_txt}",
            round(spread_last, 1),
            "contradicts" if (spread_stressed or (spread_elevated and spread_rising))
            else ("supports" if spread_last <= 0.9 * spread_median else "neutral"),
            _macro_ref(snapshot, spread_pick[0], spread_pick[1]),
        )
    if breadth is not None:
        add(
            "sector_breadth_3m",
            f"Sector index vs market over 3 months: {breadth * 100:+.1f}pp",
            round(breadth * 100, 1),
            "supports" if breadth >= 0.05 else ("contradicts" if breadth <= -0.05 else "neutral"),
            dref("sector_breadth_63d"),
        )

    # ---- instrument risk metrics ---------------------------------------------
    adj = snapshot.prices["adj_close"].dropna() if not snapshot.prices.empty else pd.Series(
        dtype=float
    )
    rets = adj.pct_change().dropna().iloc[-_BETA_WINDOW:]
    bench_rets = bench.pct_change().dropna()
    beta = stats.beta(rets, bench_rets)
    dbeta = stats.downside_beta(rets, bench_rets)
    if beta is None:
        warnings.append("too little overlapping price history for beta vs benchmark")

    vol_series = ta.realised_volatility(adj, window=_VOL_WINDOW).dropna()
    vol_1y = float(vol_series.iloc[-1]) if not vol_series.empty else None
    if vol_1y is None:
        warnings.append("fewer than 252 bars — 1-year realised volatility unavailable")

    mdd_2y = ta.max_drawdown(adj, lookback=_MDD_LOOKBACK)

    gap_rets = adj.pct_change().dropna().iloc[-_GAP_WINDOW:]
    gap_freq = (
        float((gap_rets.abs() > _GAP_THRESHOLD).mean())
        if len(gap_rets) >= _MIN_GAP_BARS
        else None
    )

    beta_worst = max((b for b in (beta, dbeta) if b is not None), default=None)
    base_parts: list[tuple[float, float | None]] = [
        (0.30, _sl(vol_1y, 0.60, 0.15)),
        (0.25, _sl(beta_worst, 2.0, 0.6)),
        (0.25, _sl(abs(mdd_2y) if mdd_2y is not None else None, 0.60, 0.10)),
        (0.20, _sl(gap_freq, 0.06, 0.0)),
    ]
    instrument_risk = _wmean(base_parts)

    if beta is not None:
        stmt = f"2-year beta vs benchmark is {beta:.2f}"
        if dbeta is not None:
            stmt += f" (downside beta {dbeta:.2f})"
        add("beta_2y", stmt, round(beta, 2),
            "supports" if (beta_worst or 0.0) <= 0.9
            else ("contradicts" if (beta_worst or 0.0) >= 1.4 else "neutral"),
            dref("beta_2y_daily"))
    if vol_1y is not None:
        add("realised_vol_1y",
            f"1-year realised volatility is {vol_1y * 100:.0f}% annualised",
            round(vol_1y * 100, 1),
            "supports" if vol_1y <= 0.25 else ("contradicts" if vol_1y >= 0.45 else "neutral"),
            dref("realised_vol_252d"))
    if mdd_2y is not None:
        add("max_drawdown_2y",
            f"Maximum drawdown over the last 2 years is {mdd_2y * 100:.1f}%",
            round(mdd_2y * 100, 1),
            "supports" if mdd_2y >= -0.20 else ("contradicts" if mdd_2y <= -0.45 else "neutral"),
            dref("max_drawdown_504d"))
    if gap_freq is not None and gap_freq > 0:
        add("gap_risk",
            f"{gap_freq * 100:.1f}% of the last year's sessions moved more than "
            f"{_GAP_THRESHOLD * 100:.0f}% — gap risk",
            round(gap_freq * 100, 2),
            "contradicts" if gap_freq >= 0.02 else "neutral",
            dref("gap_risk_freq_252d"))

    # ---- risk flags -----------------------------------------------------------
    risk_factors: list[str] = []
    deductions = 0.0
    sclass = sector_class(snapshot)

    lev_band, lev_reason, _lev_ratio = _leverage_band(snapshot, sclass)
    if not snapshot.quarterlies():
        warnings.append("no quarterly fundamentals — leverage amplification not assessed")
    if lev_band in _LEVERAGE_DEDUCTION and lev_reason is not None:
        deductions += _LEVERAGE_DEDUCTION[lev_band]
        risk_factors.append(lev_reason)
        latest = snapshot.latest_fundamental()
        add("leverage_amplification",
            f"Leverage amplifies equity risk: {lev_reason}",
            _round(_lev_ratio, 2), "contradicts",
            derived_ref(snapshot, "leverage_band",
                        based_on=latest.source if latest else pref))
    elif lev_band == "net_cash":
        latest = snapshot.latest_fundamental()
        add("leverage_amplification", "Net cash balance sheet — no leverage amplification",
            0.0, "supports",
            derived_ref(snapshot, "leverage_band",
                        based_on=latest.source if latest else pref))

    next_binary_days: int | None = None
    next_binary_kind: str | None = None
    next_binary_src: SourceRef | None = None
    for cat in snapshot.catalysts:
        if not cat.binary or cat.resolved:
            continue
        days = (cat.expected_date - snapshot.as_of).days
        if 0 <= days <= _BINARY_WINDOW_DAYS and (
            next_binary_days is None or days < next_binary_days
        ):
            next_binary_days, next_binary_kind, next_binary_src = days, cat.kind, cat.source
    binary_event_risk = next_binary_days is not None
    if binary_event_risk:
        deductions += _BINARY_DEDUCTION
        risk_factors.append(
            f"binary {next_binary_kind} event in {next_binary_days} calendar day(s)"
        )
        add("binary_event_risk",
            f"Unresolved binary {next_binary_kind} event in {next_binary_days} "
            "calendar day(s) — outcome dominates the stock either way",
            float(next_binary_days or 0), "contradicts", next_binary_src or pref)

    rate_sensitive = bool((sclass == "reit" or lev_band == "high") and rate_rising)
    if rate_sensitive and rate_pick is not None and rate_now is not None:
        deductions += _RATE_DEDUCTION
        why = "REIT" if sclass == "reit" else "high-leverage name"
        risk_factors.append(f"rate sensitivity ({why} while policy rates rise)")
        add("rate_sensitivity",
            f"Rate-sensitive {why} while the policy rate rose to {rate_now:.2f}% "
            f"over the last ~6 months",
            round(rate_now, 2), "contradicts",
            _macro_ref(snapshot, rate_pick[0], rate_pick[1]))

    fx_exposed = snapshot.info.currency != settings.base_currency
    if fx_exposed:
        deductions += _FX_DEDUCTION
        risk_factors.append(
            f"FX exposure ({snapshot.info.currency} vs {settings.base_currency} base)"
        )
        add("fx_exposure",
            f"Quoted in {snapshot.info.currency} against a {settings.base_currency} "
            "base — returns carry FX translation risk",
            snapshot.info.currency, "contradicts", dref("fx_exposure"))

    ret_12m = ta.momentum(adj, 252)
    latest_si = max(snapshot.short_interest, key=lambda r: r.as_of, default=None)
    momentum_crash_risk = bool(
        ret_12m is not None and ret_12m > _CRASH_RETURN_12M
        and latest_si is not None and latest_si.pct_float is not None
        and latest_si.pct_float > _CRASH_SHORT_PCT
        and label != "bull"
    )
    if momentum_crash_risk and latest_si is not None:
        deductions += _CRASH_DEDUCTION
        risk_factors.append(
            f"momentum-crash vulnerability (+{(ret_12m or 0) * 100:.0f}% in 12m, "
            f"{latest_si.pct_float:.1f}% of float short, regime not bull)"
        )
        add("momentum_crash_risk",
            f"Momentum-crash vulnerability: +{(ret_12m or 0) * 100:.0f}% 12-month return "
            f"with {latest_si.pct_float:.1f}% of float short in a '{label}' regime",
            round(latest_si.pct_float or 0.0, 1), "contradicts",
            derived_ref(snapshot, "momentum_crash_vulnerability", based_on=latest_si.source))

    if instrument_risk is not None:
        instrument_risk = stats.clamp(instrument_risk - deductions)
    else:
        warnings.append("instrument risk metrics not computable from available price history")

    # ---- liquidity ---------------------------------------------------------------
    traded = snapshot.liquidity.median_daily_traded_value_base
    floor = settings.universe.min_median_daily_traded_value
    spread_bps = snapshot.liquidity.spread_estimate_bps
    liq_score: float | None = None
    liq_ratio: float | None = None
    if traded is not None and floor > 0:
        liq_ratio = traded / floor
        if liq_ratio >= 8.0:
            band = "high"
        elif liq_ratio >= 2.5:
            band = "medium"
        elif liq_ratio >= 1.0:
            band = "low"
        else:
            band = "very_low"
        if spread_bps is not None and spread_bps >= _SPREAD_DOWNGRADE_BPS:
            band = _BAND_ORDER[max(0, _BAND_ORDER.index(band) - 1)]
        liq_score = _BAND_SCORE[band]
        add("liquidity_band",
            f"Median daily traded value is {traded / 1e6:.2f}m ({liq_ratio:.1f}x the "
            f"universe floor) — {band} liquidity",
            round(liq_ratio, 2),
            "supports" if band == "high"
            else ("neutral" if band == "medium" else "contradicts"),
            dref("liquidity_band"))
        if band in ("low", "very_low"):
            risk_factors.append(f"{band.replace('_', ' ')} liquidity "
                                f"({liq_ratio:.1f}x the universe floor)")
    else:
        band = "low"
        warnings.append(
            "median traded value unknown — liquidity conservatively reported as "
            "'low' and excluded from the score"
        )

    # ---- blend ------------------------------------------------------------------
    comp_raw: dict[str, float | None] = {
        "regime": env_score,
        "instrument_risk": instrument_risk,
        "liquidity_risk": liq_score,
    }
    score = _wmean([(_WEIGHTS[name], comp_raw[name]) for name in comp_raw])
    if score is None:
        return abstain(
            _ENGINE,
            "no regime/risk component computable from the available data",
            0.1,
        )
    uncomputed = [k for k, v in comp_raw.items() if v is None]
    if uncomputed:
        warnings.append(
            "components not computable from available data (shown neutral, zero weight): "
            + ", ".join(sorted(uncomputed))
        )

    # ---- risk score (10 = extremely risky) ----------------------------------------
    if instrument_risk is not None:
        risk_score = 10.0 - instrument_risk
    else:
        risk_score = 5.0
        warnings.append("risk score starts from a neutral 5.0 — instrument metrics missing")
    if label == "stress":
        risk_score += 1.0
    elif label == "bear":
        risk_score += 0.5
    if band == "very_low":
        risk_score += 0.5
    risk_score = stats.clamp(risk_score)

    # ---- details -------------------------------------------------------------------
    details = {
        "regime_label": label,
        "regime_adjustment": regime_adjustment,
        "risk_score": round(risk_score, 2),
        "risk_factors": risk_factors,
        "beta": _round(beta, 3),
        "downside_beta": _round(dbeta, 3),
        "realised_vol_1y": _round(vol_1y),
        "max_drawdown_2y": _round(mdd_2y),
        "gap_risk_freq": _round(gap_freq),
        "liquidity_band": band,
        "binary_event_risk": binary_event_risk,
        "momentum_crash_risk": momentum_crash_risk,
    }

    # ---- data quality -----------------------------------------------------------------
    dq = 0.30  # benchmark present (guaranteed at this point)
    dq += 0.15 * min(1.0, len(bench) / 240.0)
    dq += 0.15 * min(1.0, len(adj) / float(_BETA_WINDOW))
    dq += 0.10 if vix_pick is not None else 0.0
    dq += 0.05 if spread_pick is not None else 0.0
    dq += 0.05 if rate_pick is not None else 0.0
    dq += 0.05 if breadth is not None else 0.0
    dq += 0.10 if len(snapshot.quarterlies()) >= 4 else 0.0
    dq += 0.05 if traded is not None else 0.0
    if snapshot.liquidity.price_staleness_days > 3:
        warnings.append(
            f"price data stale by {snapshot.liquidity.price_staleness_days} trading days"
        )
        dq *= 0.85

    return EngineResult(
        engine=_ENGINE,
        score=round(stats.clamp(score), 2),
        components={
            name: round(value if value is not None else 5.0, 2)
            for name, value in comp_raw.items()
        },
        evidence=evidence,
        warnings=warnings,
        data_quality=round(min(1.0, dq), 2),
        details=details,
    )
