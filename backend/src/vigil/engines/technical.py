"""Multi-timeframe technical analysis engine.

How the score is built (plain English)
--------------------------------------
Three sub-scores, each 0-10 (10 = excellent, 5 = neutral), are blended with
fixed weights, then documented penalties are subtracted and the result is
clamped to 0-10:

    score = 30% trend + 40% setup + 30% confirmation - penalties

* ``trend`` (30%) — the average of three readings: (a) moving-average
  stacking: the share of bullish alignments among price > SMA20 > SMA50 >
  SMA100 > SMA200 and price > the long MA, mapped 0-10 (the 200-day leg is
  dropped when fewer than 200 bars exist); (b) MA slopes: the normalised
  per-day slopes of the SMA50 and the long MA, each mapped linearly from
  -0.1%/day (0) to +0.1%/day (10) and averaged; (c) swing structure:
  higher highs and higher lows score 8.5, lower highs/lows 1.5, mixed 5.0.
* ``setup`` (40%) — starts from the entry-setup archetype: a volume-
  confirmed breakout above the consolidation range scores 7.5 (+0.5 when
  the base was tight, band under 12%); an unconfirmed breakout 6.0;
  price within max(2 x ATR%, 3%) of a tested support zone (cluster
  strength >= 2) WITH stabilisation (Bollinger width at or below 75% of
  its 6-month median, or a bullish RSI divergence) scores 7.0; the same
  proximity without stabilisation 5.5; otherwise neutral 5.0 (4.0 when no
  tested support exists below price at all). Reward/risk — (nearest
  resistance - price) / (price - nearest-support-zone mid), capped at 5 —
  adds up to +1.5/+2.0/+1.0 on breakout / stabilised-support / bare-
  support setups (mapped linearly from 0.5x to 3.0x). An oversold RSI
  (< 30) with no qualifying setup adds at most +1.0 point, never more.
  A parabolically extended price (see penalties) is neither near tested
  support nor at a breakout any more, so the setup sub-score is capped at
  5.5 while the extension lasts and no breakout entry zone is emitted.
* ``confirmation`` (30%) — the average of: (a) volume confirmation on the
  setup: 10-day average volume over the 63-day (3-month) average, mapped
  from 0.4x (0) to 1.6x (10); neutral 5.0 when there is no setup to
  confirm, skipped entirely when volume data is degenerate; (b) relative
  strength: 3-month return vs the market benchmark mapped from -15pp (0)
  to +15pp (10), then +-1.0 depending on whether the 1-month RS run-rate
  is improving on the 3-month pace.

Penalties (each also emits contradicting evidence):

* parabolic extension — price more than 30% above the SMA50, or more than
  3 x ATR above the SMA20: -1.5 (sets ``details.extended``).
* large unfilled up-gaps beneath price (>= 4% gaps from the last 40 bars
  that never filled): -0.5 each, capped at -1.0.
* breakdown below the 200-day MA on volume — price below the SMA200 having
  been above it within the last 10 bars, with 10-day volume >= 1.2x the
  3-month average: -1.5.
* failed breakout within the last 10 bars: -1.0.

All indicators are computed on the adjusted close (ATR on raw OHLC and
expressed as a percentage of the raw close so it transfers to the adjusted
scale). The anchored VWAP anchors at the most recent past earnings or
guidance event in the snapshot's catalysts, else 6 months before as_of.
Abstains with fewer than 120 daily bars; degenerate volume data adds a
warning and drops the volume half of confirmation.
"""

from __future__ import annotations

import itertools
from datetime import timedelta
from statistics import fmean

import pandas as pd

from vigil.config import Settings
from vigil.engines.base import abstain, derived_ref, ev, price_ref
from vigil.indicators import ta
from vigil.indicators.stats import clamp, scale_linear
from vigil.schemas.core import EngineResult, Evidence, InstrumentSnapshot

ENGINE = "technical"

_WEIGHTS: dict[str, float] = {"trend": 0.30, "setup": 0.40, "confirmation": 0.30}
_MIN_BARS = 120

_STRUCTURE_SCORE = {"higher_highs_lows": 8.5, "lower_highs_lows": 1.5, "mixed": 5.0}

_PENALTY_PARABOLIC = 1.5
_PENALTY_GAP = 0.5  # per unfilled up-gap, capped at 2 gaps
_PENALTY_BREAKDOWN = 1.5
_PENALTY_FAILED_BREAKOUT = 1.0


def _last(series: pd.Series | None) -> float | None:
    """Last non-NaN-checked value of a series, or None."""
    if series is None or series.empty:
        return None
    v = series.iloc[-1]
    if pd.isna(v):
        return None
    return float(v)


def _rsi_divergence(close: pd.Series, rsi_series: pd.Series, lookback: int = 40) -> bool:
    """Bullish RSI divergence: price prints a lower low over the last
    ``lookback`` bars while RSI prints a meaningfully higher low."""
    c = close.iloc[-lookback:]
    r = rsi_series.iloc[-lookback:]
    if len(c) < lookback or r.isna().any():
        return False
    half = lookback // 2
    c1, c2 = c.iloc[:half], c.iloc[half:]
    i1, i2 = c1.idxmin(), c2.idxmin()
    return bool(
        float(c2.min()) < float(c1.min()) and float(r.loc[i2]) > float(r.loc[i1]) + 3.0
    )


def analyse(snapshot: InstrumentSnapshot, settings: Settings) -> EngineResult:
    prices = snapshot.prices
    if prices.empty or len(prices) < _MIN_BARS:
        return abstain(
            ENGINE,
            f"only {len(prices)} daily bars visible (need {_MIN_BARS})",
            round(min(len(prices) / _MIN_BARS, 1.0) * 0.5, 2),
        )
    close = prices["adj_close"].dropna()
    if len(close) < _MIN_BARS:
        return abstain(
            ENGINE, f"only {len(close)} usable adjusted closes (need {_MIN_BARS})", 0.2
        )
    price = float(close.iloc[-1])
    raw_close = _last(prices["close"].dropna())
    if price <= 0 or raw_close is None or raw_close <= 0:
        return abstain(ENGINE, "last close is missing or non-positive", 0.1)

    warnings: list[str] = []
    pref = price_ref(snapshot)

    # --- shared indicators ---------------------------------------------------
    smas = {w: ta.sma(close, w) for w in (20, 50, 100, 200)}
    last_sma = {w: _last(s) for w, s in smas.items()}
    long_w = 200 if last_sma[200] is not None else 100
    long_ma = last_sma[long_w]
    if last_sma[200] is None:
        warnings.append("fewer than 200 bars — 200-day MA unavailable")
    rsi_series = ta.rsi(close)
    rsi14 = _last(rsi_series)
    atr_last = _last(ta.atr(prices["high"], prices["low"], prices["close"]))
    atr_pct = atr_last / raw_close if atr_last is not None else None
    atr_adj = atr_pct * price if atr_pct is not None else None
    realised_vol = _last(ta.realised_volatility(close))
    macd_hist = _last(ta.macd(close)["hist"])
    dd52 = ta.drawdown_from_high(close, 252)
    mdd_1y = ta.max_drawdown(close, 252)
    mom_12_1 = ta.momentum_12_1(close)
    structure = ta.higher_highs_lows(close)
    bo = ta.breakout_state(prices)
    zones = ta.support_zones(prices)
    nearest = zones[0] if zones else None
    res_levels = ta.resistance_levels(prices)
    gaps = ta.gap_analysis(prices)

    # Anchored VWAP from the latest past earnings/guidance event, else 6m ago.
    event_dates = [
        d
        for c in snapshot.catalysts
        if c.kind in ("earnings", "guidance")
        for d in (c.expected_date, c.outcome_date)
        if d is not None and d <= snapshot.as_of
    ]
    anchor = max(event_dates) if event_dates else snapshot.as_of - timedelta(days=183)
    avwap = ta.anchored_vwap(prices, pd.Timestamp(anchor))

    # Volume health.
    vol63 = prices["volume"].iloc[-63:]
    degenerate_volume = bool(
        vol63.isna().all()
        or float(vol63.fillna(0.0).sum()) <= 0
        or float((vol63.fillna(0.0) <= 0).mean()) > 0.3
        or vol63.dropna().nunique() <= 1
    )
    vol_ratio: float | None = None
    if not degenerate_volume:
        v = prices["volume"].dropna()
        base_vol = float(v.iloc[-63:].mean())
        if base_vol > 0:
            vol_ratio = float(v.iloc[-10:].mean()) / base_vol
    else:
        warnings.append("volume data degenerate — volume-based confirmation skipped")

    # Relative strength vs market benchmark and sector index.
    bench = snapshot.benchmark.dropna() if snapshot.benchmark is not None else None
    rs_market: dict[str, float | None] = {}
    for label, win in (("1m", 21), ("3m", 63), ("6m", 126), ("12m", 252)):
        rs_market[label] = (
            ta.relative_strength(close, bench, win) if bench is not None and len(bench) else None
        )
    rs_sector_3m: float | None = None
    if snapshot.sector_index is not None:
        si = snapshot.sector_index.dropna()
        if len(si):
            rs_sector_3m = ta.relative_strength(close, si, 63)
    if rs_market["3m"] is None:
        warnings.append("benchmark series too short — relative strength unavailable")

    # --- trend (30%) ----------------------------------------------------------
    chain: list[float | None] = [price, last_sma[20], last_sma[50], last_sma[100]]
    if last_sma[200] is not None:
        chain.append(last_sma[200])
    pairs = [*itertools.pairwise(chain), (price, chain[-1])]
    valid_pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    stack_hits = sum(1 for a, b in valid_pairs if a > b)
    stack_frac = stack_hits / len(valid_pairs) if valid_pairs else 0.5
    stack_score = stack_frac * 10.0

    slope50 = ta.slope(smas[50].dropna(), 20)
    slope_long = ta.slope(smas[long_w].dropna(), 20)
    slope_parts = [
        scale_linear(s, -0.001, 0.001) for s in (slope50, slope_long) if s is not None
    ]
    slope_score = fmean(slope_parts) if slope_parts else 5.0
    structure_score = _STRUCTURE_SCORE[structure]
    trend = fmean([stack_score, slope_score, structure_score])

    s50 = last_sma[50]
    if s50 is not None and long_ma is not None and price > s50 > long_ma:
        trend_state = "uptrend"
    elif s50 is not None and long_ma is not None and price < s50 < long_ma:
        trend_state = "downtrend"
    else:
        trend_state = "range"

    # --- setup (40%) ------------------------------------------------------------
    extended = bool(
        (s50 is not None and price > 1.30 * s50)
        or (last_sma[20] is not None and atr_adj is not None
            and price > last_sma[20] + 3.0 * atr_adj)
    )
    reward_risk: float | None = None
    if nearest is not None and res_levels:
        zone_mid = (nearest["low"] + nearest["high"]) / 2.0
        risk = price - zone_mid
        if risk > 0:
            reward_risk = clamp((res_levels[0] - price) / risk, 0.0, 5.0)

    near_support = False
    support_dist_pct: float | None = None
    if nearest is not None:
        support_dist_pct = (price - nearest["high"]) / price * 100.0
        threshold_pct = max(2.0 * atr_pct * 100.0, 3.0) if atr_pct is not None else 3.0
        near_support = nearest["strength"] >= 2 and support_dist_pct <= threshold_pct

    boll = ta.bollinger(close)
    width_now = _last(boll["width"])
    width_hist = boll["width"].dropna().iloc[-126:]
    tight_range = bool(
        width_now is not None
        and len(width_hist) >= 60
        and width_now <= 0.75 * float(width_hist.median())
    )
    divergence = _rsi_divergence(close, rsi_series)
    stabilised = tight_range or divergence
    oversold = rsi14 is not None and rsi14 < 30.0

    rr_bonus = 0.0
    if bo["state"] == "breakout":
        setup_kind = "breakout"
        setup = 7.5 + (0.5 if float(bo.get("band_width", 1.0)) <= 0.12 else 0.0)
        if reward_risk is not None:
            rr_bonus = scale_linear(reward_risk, 0.5, 3.0, 0.0, 1.5)
    elif bo["state"] == "unconfirmed_breakout":
        setup_kind = "breakout"
        setup = 6.0
    elif near_support and stabilised:
        setup_kind = "support"
        setup = 7.0
        if reward_risk is not None:
            rr_bonus = scale_linear(reward_risk, 0.5, 3.0, 0.0, 2.0)
    elif near_support:
        setup_kind = "support"
        setup = 5.5
        if reward_risk is not None:
            rr_bonus = scale_linear(reward_risk, 0.5, 3.0, 0.0, 1.0)
    else:
        setup_kind = "none"
        setup = 5.0 if zones else 4.0
        if oversold:
            setup += 1.0  # oversold RSI alone adds at most one point
    setup = clamp(setup + rr_bonus)
    if extended:
        # A parabolic price is neither near tested support nor at a breakout.
        setup = min(setup, 5.5)

    # --- confirmation (30%) --------------------------------------------------------
    conf_parts: list[float] = []
    vol_conf: float | None = None
    if not degenerate_volume and vol_ratio is not None:
        vol_conf = 5.0 if setup_kind == "none" else scale_linear(vol_ratio, 0.4, 1.6)
        conf_parts.append(vol_conf)
    rs_conf: float | None = None
    rs3, rs1 = rs_market["3m"], rs_market["1m"]
    rs_improving: bool | None = None
    if rs3 is not None:
        rs_conf = scale_linear(rs3, -0.15, 0.15)
        if rs1 is not None:
            rs_improving = rs1 > rs3 * (21.0 / 63.0)
            rs_conf = clamp(rs_conf + (1.0 if rs_improving else -1.0))
        conf_parts.append(rs_conf)
    if conf_parts:
        confirmation = fmean(conf_parts)
    else:
        confirmation = 5.0
        warnings.append("no volume or benchmark confirmation available — component neutral")

    # --- penalties ---------------------------------------------------------------------
    penalties: list[tuple[str, float, str]] = []
    if extended:
        above_50 = (price / s50 - 1.0) * 100.0 if s50 else 0.0
        penalties.append(
            (
                "parabolic_extension",
                _PENALTY_PARABOLIC,
                f"Parabolic extension: price is {above_50:+.1f}% vs the 50-day MA "
                f"(threshold +30% or 3x ATR above the 20-day MA)",
            )
        )
    n_gaps = int(gaps.get("unfilled_up_gaps", 0))
    if n_gaps >= 1:
        penalties.append(
            (
                "unfilled_up_gaps",
                _PENALTY_GAP * min(n_gaps, 2),
                f"{n_gaps} large unfilled up-gap(s) sit beneath price (last 40 bars)",
            )
        )
    breakdown_200d = False
    if last_sma[200] is not None and price < last_sma[200]:
        s200_tail = smas[200].iloc[-10:]
        close_tail = close.iloc[-10:]
        was_above = bool((close_tail.to_numpy() >= s200_tail.to_numpy()).any())
        breakdown_200d = was_above and vol_ratio is not None and vol_ratio >= 1.2
    if breakdown_200d:
        penalties.append(
            (
                "breakdown_200d_on_volume",
                _PENALTY_BREAKDOWN,
                f"Price broke below the 200-day MA within 10 bars on elevated volume "
                f"({vol_ratio:.2f}x the 3-month average)",
            )
        )
    if bo["state"] == "failed_breakout":
        penalties.append(
            (
                "failed_breakout",
                _PENALTY_FAILED_BREAKOUT,
                f"Failed breakout within the last 10 bars: price fell back below the "
                f"range high {bo['range_high']:.2f}",
            )
        )
    penalty_total = sum(p for _, p, _ in penalties)
    for name, _, _ in penalties:
        warnings.append(f"penalty applied: {name}")

    # --- blend -----------------------------------------------------------------------
    components = {
        "trend": round(clamp(trend), 2),
        "setup": round(setup, 2),
        "confirmation": round(clamp(confirmation), 2),
    }
    score = clamp(sum(components[k] * w for k, w in _WEIGHTS.items()) - penalty_total)

    # --- hints -------------------------------------------------------------------------
    stop_hint: float | None = None
    if nearest is not None and atr_adj is not None:
        stop_hint = round(nearest["low"] - atr_adj, 2)
    entry_zone_hint: dict[str, float] | None = None
    if setup_kind == "support" and nearest is not None and setup >= 6.5:
        entry_zone_hint = {"low": round(nearest["low"], 2), "high": round(nearest["high"], 2)}
    elif bo["state"] == "breakout" and not extended and price > float(bo["range_high"]) > 0:
        entry_zone_hint = {"low": round(float(bo["range_high"]), 2), "high": round(price, 2)}

    # --- evidence -------------------------------------------------------------------
    evidence: list[Evidence] = []
    ind_ref = derived_ref(snapshot, "technical_indicators", based_on=pref)

    stack_dir = "supports" if stack_frac >= 0.8 else "contradicts" if stack_frac <= 0.2 else "neutral"
    evidence.append(
        ev(
            snapshot,
            "ma_stack",
            f"{stack_hits}/{len(valid_pairs)} bullish moving-average alignments "
            f"(price vs 20/50/100{'/200' if last_sma[200] is not None else ''}-day SMAs)",
            float(stack_hits),
            stack_dir,  # type: ignore[arg-type]
            "technical",
            derived_ref(snapshot, "ma_stack", based_on=pref),
        )
    )
    if long_ma is not None:
        gap_long = (price / long_ma - 1.0) * 100.0
        d = "supports" if gap_long > 0 else "contradicts"
        slope_txt = f", slope {slope_long * 100:+.3f}%/day" if slope_long is not None else ""
        evidence.append(
            ev(
                snapshot,
                f"price_vs_sma{long_w}",
                f"Price is {gap_long:+.1f}% vs the {long_w}-day MA{slope_txt}",
                round(gap_long, 2),
                d,  # type: ignore[arg-type]
                "technical",
                ind_ref,
            )
        )
    evidence.append(
        ev(
            snapshot,
            "swing_structure",
            f"Swing structure over ~6 months is {structure.replace('_', ' ')}",
            structure,
            (
                "supports"
                if structure == "higher_highs_lows"
                else "contradicts"
                if structure == "lower_highs_lows"
                else "neutral"
            ),  # type: ignore[arg-type]
            "technical",
            derived_ref(snapshot, "higher_highs_lows", based_on=pref),
        )
    )
    bo_dir = (
        "supports"
        if bo["state"] == "breakout"
        else "contradicts"
        if bo["state"] == "failed_breakout"
        else "neutral"
    )
    bo_txt = f"Breakout state is '{bo['state']}'"
    if "volume_ratio" in bo:
        bo_txt += f" (recent volume {bo['volume_ratio']:.2f}x the base)"
    evidence.append(
        ev(
            snapshot,
            "breakout_state",
            bo_txt,
            str(bo["state"]),
            bo_dir,  # type: ignore[arg-type]
            "technical",
            derived_ref(snapshot, "breakout_state", based_on=pref),
        )
    )
    if nearest is not None and support_dist_pct is not None:
        d = "supports" if near_support and stabilised else "neutral"
        stab_txt = (
            " with stabilisation" if near_support and stabilised
            else " without stabilisation" if near_support else ""
        )
        evidence.append(
            ev(
                snapshot,
                "support_proximity",
                f"Price is {support_dist_pct:+.1f}% above the nearest tested support zone "
                f"{nearest['low']:.2f}-{nearest['high']:.2f} "
                f"(strength {nearest['strength']}){stab_txt}",
                round(support_dist_pct, 2),
                d,  # type: ignore[arg-type]
                "technical",
                derived_ref(snapshot, "support_zones", based_on=pref),
            )
        )
    if reward_risk is not None:
        d = "supports" if reward_risk >= 2.0 else "contradicts" if reward_risk < 1.0 else "neutral"
        evidence.append(
            ev(
                snapshot,
                "reward_risk",
                f"Reward/risk to the nearest resistance {res_levels[0]:.2f} over the nearest "
                f"support zone is {reward_risk:.1f}x (capped at 5)",
                round(reward_risk, 2),
                d,  # type: ignore[arg-type]
                "technical",
                derived_ref(snapshot, "reward_risk", based_on=pref),
            )
        )
    if vol_ratio is not None:
        d = (
            "supports"
            if setup_kind != "none" and vol_ratio >= 1.3
            else "neutral"
        )
        evidence.append(
            ev(
                snapshot,
                "volume_vs_3m",
                f"10-day average volume is {vol_ratio:.2f}x the 3-month average",
                round(vol_ratio, 2),
                d,  # type: ignore[arg-type]
                "technical",
                derived_ref(snapshot, "volume_ratio_10d_63d", based_on=pref),
            )
        )
    if rs3 is not None:
        d = "supports" if rs3 > 0.05 else "contradicts" if rs3 < -0.05 else "neutral"
        improving_txt = (
            ", improving" if rs_improving else ", deteriorating" if rs_improving is False else ""
        )
        evidence.append(
            ev(
                snapshot,
                "rs_3m_market",
                f"3-month relative strength vs the market benchmark is "
                f"{rs3 * 100:+.1f}pp{improving_txt}",
                round(rs3 * 100, 2),
                d,  # type: ignore[arg-type]
                "technical",
                derived_ref(snapshot, "relative_strength", based_on=pref),
            )
        )
    for key, _, statement in penalties:
        evidence.append(
            ev(
                snapshot,
                key,
                statement,
                None,
                "contradicts",
                "technical",
                derived_ref(snapshot, key, based_on=pref),
            )
        )
    tail: list[Evidence] = []
    if rsi14 is not None:
        d = "contradicts" if rsi14 >= 75 else "neutral"
        note = " (oversold — worth at most a mild positive without support)" if oversold else ""
        tail.append(
            ev(
                snapshot,
                "rsi14",
                f"14-day RSI is {rsi14:.1f}{note}",
                round(rsi14, 2),
                d,  # type: ignore[arg-type]
                "technical",
                derived_ref(snapshot, "rsi", based_on=pref),
            )
        )
    if dd52 is not None:
        tail.append(
            ev(
                snapshot,
                "drawdown_52w",
                f"Price is {dd52 * 100:+.1f}% from its 52-week high",
                round(dd52 * 100, 2),
                "neutral",
                "technical",
                derived_ref(snapshot, "drawdown_from_high", based_on=pref),
            )
        )
    if avwap is not None and avwap > 0:
        vs_avwap = (price / avwap - 1.0) * 100.0
        tail.append(
            ev(
                snapshot,
                "anchored_vwap",
                f"Price is {vs_avwap:+.1f}% vs the VWAP anchored at {anchor.isoformat()}",
                round(vs_avwap, 2),
                "neutral",
                "technical",
                derived_ref(snapshot, "anchored_vwap", based_on=pref),
            )
        )
    if mom_12_1 is not None:
        d = "supports" if mom_12_1 > 0.15 else "contradicts" if mom_12_1 < -0.15 else "neutral"
        tail.append(
            ev(
                snapshot,
                "momentum_12_1",
                f"12-1 month momentum is {mom_12_1 * 100:+.1f}%",
                round(mom_12_1 * 100, 2),
                d,  # type: ignore[arg-type]
                "technical",
                derived_ref(snapshot, "momentum_12_1", based_on=pref),
            )
        )
    if macd_hist is not None:
        tail.append(
            ev(
                snapshot,
                "macd_histogram",
                f"MACD histogram is {macd_hist:+.3f}",
                round(macd_hist, 4),
                "neutral",
                "technical",
                derived_ref(snapshot, "macd", based_on=pref),
            )
        )
    evidence = (evidence + tail)[:12]

    # --- data quality --------------------------------------------------------------
    checks = [
        len(close) >= 252,
        last_sma[200] is not None,
        not degenerate_volume,
        snapshot.liquidity.price_staleness_days <= settings.gates.max_price_staleness_days,
        rs3 is not None,
        snapshot.sector_index is not None,
    ]
    data_quality = clamp((2 + sum(checks)) / (2 + len(checks)), 0.0, 1.0)

    details: dict[str, object] = {
        "support_zones": [
            {
                "low": round(z["low"], 4),
                "high": round(z["high"], 4),
                "strength": z["strength"],
                "basis": z["basis"],
            }
            for z in zones
        ],
        "resistance_levels": [round(r, 4) for r in res_levels],
        "nearest_support": (
            {"low": round(nearest["low"], 4), "high": round(nearest["high"], 4)}
            if nearest is not None
            else None
        ),
        "stop_hint": stop_hint,
        "reward_risk": round(reward_risk, 2) if reward_risk is not None else None,
        "entry_zone_hint": entry_zone_hint,
        "breakout": bo,
        "trend_state": trend_state,
        "extended": extended,
        "atr_pct": round(atr_pct, 4) if atr_pct is not None else None,
        "realised_vol_annual": round(realised_vol, 4) if realised_vol is not None else None,
        "drawdown_from_52w_high_pct": round(dd52 * 100, 2) if dd52 is not None else None,
        "rsi14": round(rsi14, 2) if rsi14 is not None else None,
        "above_sma200": (price > last_sma[200]) if last_sma[200] is not None else None,
        "anchored_vwap": round(avwap, 4) if avwap is not None else None,
        "rs_3m_market": round(rs3, 4) if rs3 is not None else None,
        # extras (not part of the promised contract, useful to signal rules)
        "setup_kind": setup_kind,
        "penalties": [name for name, _, _ in penalties],
        "max_drawdown_1y": round(mdd_1y, 4) if mdd_1y is not None else None,
        "rs_by_window_market": {
            k: (round(v, 4) if v is not None else None) for k, v in rs_market.items()
        },
        "rs_3m_sector": round(rs_sector_3m, 4) if rs_sector_3m is not None else None,
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
