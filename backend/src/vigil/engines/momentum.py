"""Cross-sectional and fundamental momentum engine.

Score construction (plain English)
----------------------------------
Three sub-scores, each 0-10 (5 = neutral), are blended into the engine
score. When a sub-score cannot be computed from the available data its
weight is dropped and the remaining weights renormalise (a warning is
added; the components dict then shows a neutral 5.0 for that entry):

* ``price_momentum`` (45%) — total returns over 1/3/6/12 months and the
  classic 12-minus-1-month return, each mapped linearly onto 0-10
  (1m: -12%..+12%, 3m: -25%..+40%, 6m: -35%..+60%, 12m and 12-1:
  -50%..+100%) and blended with weights 5/15/20/25/35 — the classic
  12-minus-1 window carries the most weight.
* ``fundamental_momentum`` (35%) — from estimate snapshots no older than
  120 days for periods that have not yet ended (EPS preferred over
  revenue): revision breadth over 30 days ((up - down) / analyst count,
  mapped from -40% to +40%, weight 15), revision magnitude over 90 days
  ((mean - mean 90d ago) / |mean 90d ago|, mapped from -4% to +4%,
  weight 20), the average EPS surprise from earnings-catalyst outcomes
  in the last ~200 days (parsed from the "EPS surprise ±x%" pattern;
  unparseable outcomes are treated as absent; mapped from -8% to +8%,
  weight 35), guidance events from factual news in the last 120 days
  (mean provider sentiment mapped from -0.75 to +0.75, weight 15), and
  margin momentum (weight 15: 8.5 when TTM operating margin turned
  positive or rose >= 3pp YoY, 2.0 when it turned negative or fell
  >= 3pp YoY, else 5.0; needs 8 quarters of fundamentals).
* ``confirmation`` (20%) — relative strength vs the market benchmark over
  1/3/6 months (mapped from -8%..+8%, -15%..+15%, -25%..+25%; weights
  10/30/20), relative strength vs the sector index over 3 months
  (-12%..+12%, weight 15), and 3-month volume confirmation (mean up-day
  volume / mean down-day volume, mapped from 0.75 to 1.50, weight 25).
  A confirmed accumulation breakout (breakout state ``breakout`` with
  volume ratio >= 1.3 after a base of at least 60 bars) adds +1.0 to
  this component.

Adjustments applied to the blended score:

* Confluence: +0.75 when price momentum (6m return, falling back to 3m),
  net revisions (90d magnitude, falling back to breadth) and relative
  strength (3m vs market, falling back to 6m) all agree positively;
  -0.75 when all three agree negatively.
* Penalties: each triggered penalty subtracts 0.75 (total capped at
  -3.0), adds contradicting evidence, and is listed in
  ``details.penalties``: parabolic extension (3m return > 50% and price
  > 30% above the 50-day SMA), crowding (short interest > 10% of float
  and rising between the last two observations, or social items > 40% of
  news flow over 30 days with at least 5 items), low-liquidity move
  (3m return > 15% while median traded value is below the universe
  liquidity floor), unfilled up-gap of > 5% beneath the price (last 40
  bars), an unresolved binary catalyst within ~10 trading days (14
  calendar days), negative divergence (3m return > 5% while net
  revisions are down — breadth and 90d magnitude both negative, or the
  only available one clearly negative), and deteriorating fundamentals
  under price strength (TTM revenue below its year-ago level while the
  6m return exceeds +20%).

The final score is clamped to [0, 10]. Abstains with fewer than 260
daily bars (a full 12 months plus margin is required).
"""

from __future__ import annotations

import re
from datetime import date
from statistics import fmean

from vigil.config import Settings
from vigil.engines.base import abstain, derived_ref, ev, price_ref
from vigil.indicators import ta
from vigil.indicators.stats import clamp, scale_linear
from vigil.schemas.core import (
    CatalystRecord,
    Direction,
    EngineResult,
    EstimateRecord,
    Evidence,
    InstrumentSnapshot,
    NewsRecord,
    SourceRef,
)

_ENGINE = "momentum"
_MIN_BARS = 260
_PENALTY = 0.75
_PENALTY_CAP = 3.0
_CONFLUENCE_ADJ = 0.75
_BREAKOUT_BONUS = 1.0
_GUIDANCE_WINDOW_DAYS = 120
_ESTIMATE_STALE_DAYS = 120
_SURPRISE_WINDOW_DAYS = 200
_BINARY_WINDOW_DAYS = 14  # ~10 trading days
_SOCIAL_WINDOW_DAYS = 30
_SOCIAL_SHARE_LIMIT = 0.40
_SOCIAL_MIN_ITEMS = 5
_MIN_CONSOLIDATION_BARS = 60
_SURPRISE_RE = re.compile(r"EPS surprise\s*([+-]?\d+(?:\.\d+)?)\s*%", re.IGNORECASE)

_WEIGHTS: dict[str, float] = {
    "price_momentum": 0.45,
    "fundamental_momentum": 0.35,
    "confirmation": 0.20,
}


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
    return scale_linear(value, worst, best)


def _direction(score: float | None, hi: float = 6.5, lo: float = 3.5) -> Direction:
    if score is None:
        return "neutral"
    if score >= hi:
        return "supports"
    if score <= lo:
        return "contradicts"
    return "neutral"


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def _estimate_revisions(
    estimates: tuple[EstimateRecord, ...], as_of: date
) -> tuple[float | None, float | None, EstimateRecord | None]:
    """(breadth_30d, magnitude_90d, freshest record). Only estimate
    snapshots taken within the last 120 days for periods that have not
    yet ended are momentum-relevant; EPS estimates are preferred, other
    metrics are the fallback when no fresh EPS estimates exist."""
    fresh = [
        e for e in estimates
        if 0 <= (as_of - e.as_of).days <= _ESTIMATE_STALE_DAYS and e.period_end >= as_of
    ]
    eps = [e for e in fresh if e.metric == "eps"]
    used = eps if eps else fresh
    if not used:
        return None, None, None
    counted = [e for e in used if e.analyst_count > 0]
    breadth: float | None = None
    if counted:
        analysts = sum(e.analyst_count for e in counted)
        net = sum(e.up_revisions_30d - e.down_revisions_30d for e in counted)
        breadth = net / analysts
    rels: list[float] = []
    for e in used:
        if e.mean_90d_ago is not None and abs(e.mean_90d_ago) > 1e-9:
            rel = (e.mean - e.mean_90d_ago) / abs(e.mean_90d_ago)
            rels.append(max(-1.0, min(1.0, rel)))
    magnitude = fmean(rels) if rels else None
    freshest = max(used, key=lambda e: (e.as_of, e.fiscal_label, e.metric))
    return breadth, magnitude, freshest


def _earnings_surprises(
    catalysts: tuple[CatalystRecord, ...], as_of: date
) -> tuple[float | None, float | None, CatalystRecord | None]:
    """(last surprise %, mean recent surprise %, latest record) parsed from
    resolved earnings-catalyst outcomes; unparseable outcomes are absent."""
    parsed: list[tuple[date, float, CatalystRecord]] = []
    for c in catalysts:
        if c.kind != "earnings" or not c.resolved or not c.outcome:
            continue
        m = _SURPRISE_RE.search(c.outcome)
        if m is None:
            continue
        when = c.outcome_date or c.expected_date
        parsed.append((when, float(m.group(1)), c))
    if not parsed:
        return None, None, None
    parsed.sort(key=lambda t: (t[0], t[2].record_id))
    _, last_val, last_rec = parsed[-1]
    recent = [v for when, v, _ in parsed if 0 <= (as_of - when).days <= _SURPRISE_WINDOW_DAYS]
    return last_val, (fmean(recent) if recent else None), last_rec


def _guidance_events(
    news: tuple[NewsRecord, ...], as_of: date
) -> tuple[float | None, NewsRecord | None]:
    """Mean sentiment of factual guidance headlines in the last 120 days."""
    events = [
        n
        for n in news
        if n.source_type == "factual_event"
        and "guidance" in n.headline.lower()
        and 0 <= (as_of - n.published_at.date()).days <= _GUIDANCE_WINDOW_DAYS
    ]
    if not events:
        return None, None
    latest = max(events, key=lambda n: (n.published_at, n.record_id))
    return fmean(n.sentiment for n in events), latest


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


def analyse(snapshot: InstrumentSnapshot, settings: Settings) -> EngineResult:
    """Cross-sectional + fundamental momentum (see module docstring)."""
    px = snapshot.prices
    if len(px) < _MIN_BARS:
        return abstain(
            _ENGINE,
            f"insufficient price history: {len(px)} bars visible, need at least {_MIN_BARS}",
            data_quality=round(min(0.5, len(px) / _MIN_BARS * 0.5), 2),
        )
    adj = px["adj_close"].dropna()
    if len(adj) < _MIN_BARS:
        return abstain(
            _ENGINE,
            f"insufficient usable adjusted closes: {len(adj)} of {_MIN_BARS} needed",
            data_quality=0.2,
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
        evidence.append(ev(snapshot, key, statement, value, direction, "momentum", source))

    def dref(formula: str, based_on: SourceRef | None = None) -> SourceRef:
        return derived_ref(snapshot, formula, based_on=based_on or pref)

    # ---- price momentum ----------------------------------------------------
    m1 = ta.momentum(adj, 21)
    m3 = ta.momentum(adj, 63)
    m6 = ta.momentum(adj, 126)
    m12 = ta.momentum(adj, 252)
    m12_1 = ta.momentum_12_1(adj)

    price_parts: list[tuple[float, float | None]] = [
        (0.05, _sl(m1, -0.12, 0.12)),
        (0.15, _sl(m3, -0.25, 0.40)),
        (0.20, _sl(m6, -0.35, 0.60)),
        (0.25, _sl(m12, -0.50, 1.00)),
        (0.35, _sl(m12_1, -0.50, 1.00)),
    ]
    price_score = _wmean(price_parts)

    if m12 is not None:
        stmt = f"12-month price return is {m12 * 100:+.1f}%"
        if m12_1 is not None:
            stmt += f" ({m12_1 * 100:+.1f}% excluding the most recent month)"
        add("price_return_12m", stmt, round(m12 * 100, 1),
            _direction(_sl(m12, -0.50, 1.00)), dref("momentum_252d"))
    if m3 is not None:
        stmt = f"3-month price return is {m3 * 100:+.1f}%"
        if m6 is not None:
            stmt += f"; 6-month is {m6 * 100:+.1f}%"
        add("price_return_3m", stmt, round(m3 * 100, 1),
            _direction(_sl(m3, -0.25, 0.40)), dref("momentum_63d"))

    # ---- relative strength ---------------------------------------------------
    bench = snapshot.benchmark
    have_bench = bench is not None and len(bench.dropna()) > 0
    rs_1m = ta.relative_strength(adj, bench, 21) if have_bench else None
    rs_3m = ta.relative_strength(adj, bench, 63) if have_bench else None
    rs_6m = ta.relative_strength(adj, bench, 126) if have_bench else None
    if not have_bench:
        warnings.append("benchmark series empty — market relative strength unavailable")
    sector = snapshot.sector_index
    rs_sector_3m = (
        ta.relative_strength(adj, sector, 63)
        if sector is not None and len(sector.dropna()) > 0
        else None
    )
    if rs_3m is not None:
        add("rs_market_3m",
            f"3-month relative strength vs market benchmark is {rs_3m * 100:+.1f}pp",
            round(rs_3m * 100, 1), _direction(_sl(rs_3m, -0.15, 0.15)),
            dref("relative_strength_63d"))

    # ---- volume confirmation --------------------------------------------------
    vol_tail = px["volume"].iloc[-63:]
    volume_ok = float(vol_tail.fillna(0).sum()) > 0 and vol_tail.nunique() > 1
    updown: float | None = None
    if volume_ok:
        tail = px.iloc[-63:]
        chg = tail["adj_close"].diff()
        up_vol = tail.loc[chg > 0, "volume"].dropna()
        down_vol = tail.loc[chg < 0, "volume"].dropna()
        if len(up_vol) >= 5 and len(down_vol) >= 5 and float(down_vol.mean()) > 0:
            updown = float(up_vol.mean()) / float(down_vol.mean())
    else:
        warnings.append("volume data degenerate — volume confirmation skipped")
    if updown is not None:
        add("up_down_volume_3m",
            f"Up-day volume is {updown:.2f}x down-day volume over 3 months",
            round(updown, 2), _direction(_sl(updown, 0.75, 1.50)),
            dref("up_down_volume_ratio_63d"))

    # ---- accumulation breakout -------------------------------------------------
    bstate = ta.breakout_state(px, lookback=126)
    base_bars = min(len(adj), 126) - 10
    accumulation_breakout = bool(
        bstate.get("state") == "breakout"
        and float(bstate.get("volume_ratio", 0.0)) >= 1.3
        and base_bars >= _MIN_CONSOLIDATION_BARS
    )
    if accumulation_breakout:
        add("accumulation_breakout",
            f"Confirmed breakout above a {base_bars}-bar base on "
            f"{bstate.get('volume_ratio', 0.0):.2f}x volume",
            float(bstate.get("volume_ratio", 0.0)), "supports", dref("breakout_state_126d"))

    confirmation_parts: list[tuple[float, float | None]] = [
        (0.10, _sl(rs_1m, -0.08, 0.08)),
        (0.30, _sl(rs_3m, -0.15, 0.15)),
        (0.20, _sl(rs_6m, -0.25, 0.25)),
        (0.15, _sl(rs_sector_3m, -0.12, 0.12)),
        (0.25, _sl(updown, 0.75, 1.50)),
    ]
    confirmation_score = _wmean(confirmation_parts)
    if confirmation_score is not None and accumulation_breakout:
        confirmation_score = clamp(confirmation_score + _BREAKOUT_BONUS)

    # ---- fundamental momentum ----------------------------------------------------
    breadth, magnitude, est_rec = _estimate_revisions(snapshot.estimates, snapshot.as_of)
    if not snapshot.estimates:
        warnings.append("no analyst estimates visible — revision momentum unavailable")
    elif est_rec is None:
        warnings.append(
            "estimate snapshots are stale or backward-looking — revision momentum unavailable"
        )
    surprise_last, surprise_avg, surprise_rec = _earnings_surprises(
        snapshot.catalysts, snapshot.as_of
    )
    guidance_mean, guidance_rec = _guidance_events(snapshot.news, snapshot.as_of)

    qs = snapshot.quarterlies()
    margin_inflection = False
    margin_broke = False
    margin_part: float | None = None
    margin_now: float | None = None
    margin_prev: float | None = None
    if len(qs) >= 8:
        now4, prev4 = qs[-4:], qs[-8:-4]
        vals = [q.revenue for q in now4 + prev4] + [q.operating_income for q in now4 + prev4]
        if all(v is not None for v in vals):
            rev_now = sum(q.revenue for q in now4)  # type: ignore[misc]
            rev_prev = sum(q.revenue for q in prev4)  # type: ignore[misc]
            if rev_now > 0 and rev_prev > 0:
                margin_now = sum(q.operating_income for q in now4) / rev_now  # type: ignore[misc]
                margin_prev = sum(q.operating_income for q in prev4) / rev_prev  # type: ignore[misc]
                margin_inflection = bool(
                    (margin_prev <= 0.0 < margin_now) or (margin_now - margin_prev >= 0.03)
                )
                margin_broke = bool(
                    (margin_prev > 0.0 >= margin_now) or (margin_now - margin_prev <= -0.03)
                )
                margin_part = 8.5 if margin_inflection else (2.0 if margin_broke else 5.0)
    else:
        warnings.append("fewer than 8 quarterly reports — margin inflection not assessable")

    fundamental_parts: list[tuple[float, float | None]] = [
        (0.15, _sl(breadth, -0.40, 0.40)),
        (0.20, _sl(magnitude, -0.04, 0.04)),
        (0.35, _sl(surprise_avg, -8.0, 8.0)),
        (0.15, _sl(guidance_mean, -0.75, 0.75)),
        (0.15, margin_part),
    ]
    fundamental_score = _wmean(fundamental_parts)

    if breadth is not None and est_rec is not None:
        add("revision_breadth_30d",
            f"Net estimate revision breadth (30d) is {breadth * 100:+.0f}% of analysts",
            round(breadth, 3), _direction(_sl(breadth, -0.40, 0.40)),
            derived_ref(snapshot, "revision_breadth_30d", based_on=est_rec.source))
    if magnitude is not None and est_rec is not None:
        add("revision_magnitude_90d",
            f"Consensus estimate moved {magnitude * 100:+.1f}% over the last 90 days",
            round(magnitude * 100, 2), _direction(_sl(magnitude, -0.04, 0.04)),
            derived_ref(snapshot, "revision_magnitude_90d", based_on=est_rec.source))
    if surprise_last is not None and surprise_rec is not None:
        add("earnings_surprise_last",
            f"Last earnings surprise was {surprise_last:+.1f}% vs consensus EPS",
            round(surprise_last, 2), _direction(_sl(surprise_last, -8.0, 8.0)),
            surprise_rec.source)
    if guidance_mean is not None and guidance_rec is not None:
        add("guidance_event",
            f"Guidance news in the last {_GUIDANCE_WINDOW_DAYS} days: "
            f"\"{guidance_rec.headline}\" (mean sentiment {guidance_mean:+.2f})",
            round(guidance_mean, 2),
            "supports" if guidance_mean > 0.15
            else ("contradicts" if guidance_mean < -0.15 else "neutral"),
            guidance_rec.source)
    if margin_inflection and margin_now is not None and margin_prev is not None:
        add("margin_inflection",
            f"TTM operating margin inflected: {margin_now * 100:.1f}% vs "
            f"{margin_prev * 100:.1f}% a year ago",
            round((margin_now - margin_prev) * 100, 1), "supports",
            derived_ref(snapshot, "margin_inflection_yoy", based_on=qs[-1].source))
    elif margin_broke and margin_now is not None and margin_prev is not None:
        add("margin_deterioration",
            f"TTM operating margin deteriorated: {margin_now * 100:.1f}% vs "
            f"{margin_prev * 100:.1f}% a year ago",
            round((margin_now - margin_prev) * 100, 1), "contradicts",
            derived_ref(snapshot, "margin_inflection_yoy", based_on=qs[-1].source))

    # ---- blend --------------------------------------------------------------
    comp_raw: dict[str, float | None] = {
        "price_momentum": price_score,
        "fundamental_momentum": fundamental_score,
        "confirmation": confirmation_score,
    }
    score = _wmean([(_WEIGHTS[name], comp_raw[name]) for name in comp_raw])
    if score is None:
        return abstain(_ENGINE, "price series not usable for momentum measurement", 0.2)
    uncomputed = [k for k, v in comp_raw.items() if v is None]
    if uncomputed:
        warnings.append(
            "components not computable from available data (shown neutral, zero weight): "
            + ", ".join(sorted(uncomputed))
        )

    # ---- confluence -----------------------------------------------------------
    price_sig = m6 if m6 is not None else m3
    rev_sig = magnitude if magnitude is not None else breadth
    rs_sig = rs_3m if rs_3m is not None else rs_6m
    if price_sig is not None and rev_sig is not None and rs_sig is not None:
        if price_sig > 0 and rev_sig > 0 and rs_sig > 0:
            score += _CONFLUENCE_ADJ
            add("momentum_confluence",
                "Price momentum, estimate revisions and relative strength all positive — "
                "confluence bonus applied",
                None, "supports", dref("momentum_confluence"))
        elif price_sig < 0 and rev_sig < 0 and rs_sig < 0:
            score -= _CONFLUENCE_ADJ
            add("momentum_confluence",
                "Price momentum, estimate revisions and relative strength all negative — "
                "confluence in the wrong direction",
                None, "contradicts", dref("momentum_confluence"))

    # ---- penalties --------------------------------------------------------------
    penalties: list[str] = []
    last_px = float(adj.iloc[-1])
    sma50 = ta.sma(adj, 50).dropna()
    sma50_last = float(sma50.iloc[-1]) if not sma50.empty else None

    parabolic = bool(
        m3 is not None and m3 > 0.50 and sma50_last is not None and sma50_last > 0
        and last_px > 1.30 * sma50_last
    )
    if parabolic:
        penalties.append("parabolic")
        m3_pct = (m3 or 0.0) * 100
        sma_gap = (last_px / sma50_last - 1) * 100 if sma50_last else 0.0
        add("penalty_parabolic",
            f"Parabolic extension: +{m3_pct:.0f}% in 3 months and price "
            f"{sma_gap:.0f}% above the 50-day average",
            round(m3_pct, 1), "contradicts", dref("parabolic_extension"))

    si = sorted(snapshot.short_interest, key=lambda r: r.as_of)
    si_crowded = bool(
        len(si) >= 2
        and si[-1].pct_float is not None
        and si[-2].pct_float is not None
        and si[-1].pct_float > 10.0
        and si[-1].pct_float > si[-2].pct_float
    )
    recent_news = [
        n for n in snapshot.news
        if 0 <= (snapshot.as_of - n.published_at.date()).days <= _SOCIAL_WINDOW_DAYS
    ]
    social_share: float | None = None
    if len(recent_news) >= _SOCIAL_MIN_ITEMS:
        social_share = sum(1 for n in recent_news if n.source_type == "social") / len(recent_news)
    social_crowded = bool(social_share is not None and social_share > _SOCIAL_SHARE_LIMIT)
    if si_crowded or social_crowded:
        penalties.append("crowding")
        if si_crowded:
            add("penalty_crowding",
                f"Crowding: short interest is {si[-1].pct_float or 0.0:.1f}% of float and rising",
                round(si[-1].pct_float or 0.0, 2), "contradicts", si[-1].source)
        else:
            add("penalty_crowding",
                f"Crowding: social posts are {(social_share or 0.0) * 100:.0f}% of news flow "
                f"over the last {_SOCIAL_WINDOW_DAYS} days",
                round((social_share or 0.0) * 100, 1), "contradicts",
                dref("social_share_of_news_30d"))

    traded = snapshot.liquidity.median_daily_traded_value_base
    if (
        m3 is not None and m3 > 0.15 and traded is not None
        and traded < settings.universe.min_median_daily_traded_value
    ):
        penalties.append("low_liquidity_move")
        add("penalty_low_liquidity_move",
            f"+{m3 * 100:.0f}% 3-month move on thin trading "
            f"({traded / 1e6:.2f}m/day vs "
            f"{settings.universe.min_median_daily_traded_value / 1e6:.1f}m floor)",
            round(traded / 1e6, 2), "contradicts", dref("low_liquidity_move"))

    gaps = ta.gap_analysis(px, lookback=40, threshold=0.05)
    if int(gaps.get("unfilled_up_gaps", 0)) > 0:
        penalties.append("unfilled_gap_below")
        add("penalty_unfilled_gap",
            f"{gaps['unfilled_up_gaps']} unfilled up-gap(s) larger than 5% sit beneath "
            "the price — air-pocket risk",
            float(gaps["unfilled_up_gaps"]), "contradicts", dref("gap_analysis_40d"))

    next_binary: CatalystRecord | None = None
    next_binary_days: int | None = None
    for c in snapshot.catalysts:
        if not c.binary or c.resolved:
            continue
        days = (c.expected_date - snapshot.as_of).days
        if 0 <= days <= _BINARY_WINDOW_DAYS and (
            next_binary_days is None or days < next_binary_days
        ):
            next_binary, next_binary_days = c, days
    if next_binary is not None:
        penalties.append("binary_event_within_10d")
        add("penalty_binary_event",
            f"Binary {next_binary.kind} event in {next_binary_days} calendar day(s) "
            f"({next_binary.expected_date.isoformat()}) — momentum can gap either way",
            float(next_binary_days or 0), "contradicts", next_binary.source)

    revisions_down = False
    if breadth is not None and magnitude is not None:
        revisions_down = breadth < -0.01 and magnitude < -0.005
    elif breadth is not None:
        revisions_down = breadth < -0.05
    elif magnitude is not None:
        revisions_down = magnitude < -0.02
    if m3 is not None and m3 > 0.05 and revisions_down:
        add("penalty_negative_divergence",
            f"Price is up {m3 * 100:+.1f}% over 3 months while net estimate revisions "
            "are falling — negative divergence",
            round(breadth if breadth is not None else magnitude or 0.0, 3), "contradicts",
            derived_ref(snapshot, "negative_divergence",
                        based_on=est_rec.source if est_rec else pref))
        penalties.append("negative_divergence")

    if len(qs) >= 8 and m6 is not None and m6 > 0.20:
        rev_now4 = [q.revenue for q in qs[-4:]]
        rev_prev4 = [q.revenue for q in qs[-8:-4]]
        if all(v is not None for v in rev_now4 + rev_prev4):
            ttm_now = float(sum(rev_now4))  # type: ignore[arg-type]
            ttm_prev = float(sum(rev_prev4))  # type: ignore[arg-type]
            if ttm_now < ttm_prev:
                penalties.append("deteriorating_fundamentals")
                add("penalty_deteriorating_fundamentals",
                    f"TTM revenue is shrinking ({(ttm_now / ttm_prev - 1) * 100:+.1f}% YoY) "
                    f"while the price is up {m6 * 100:+.0f}% over 6 months",
                    round((ttm_now / ttm_prev - 1) * 100, 1), "contradicts",
                    derived_ref(snapshot, "revenue_vs_price_divergence",
                                based_on=qs[-1].source))

    if penalties:
        score -= min(_PENALTY_CAP, _PENALTY * len(penalties))
        warnings.append(f"{len(penalties)} momentum penalty(ies) — see details.penalties")

    # ---- details -----------------------------------------------------------------
    details = {
        "returns": {
            "m1": _round(m1), "m3": _round(m3), "m6": _round(m6),
            "m12": _round(m12), "m12_1": _round(m12_1),
        },
        "rs": {
            "market_1m": _round(rs_1m), "market_3m": _round(rs_3m),
            "market_6m": _round(rs_6m), "sector_3m": _round(rs_sector_3m),
        },
        "revision_breadth_30d": _round(breadth),
        "revision_magnitude_90d": _round(magnitude),
        "surprise_last": round(surprise_last, 2) if surprise_last is not None else None,
        "margin_inflection": margin_inflection,
        "penalties": penalties,
        "parabolic": parabolic,
        "accumulation_breakout": accumulation_breakout,
    }

    # ---- data quality ---------------------------------------------------------------
    dq = 0.35  # sufficient price history (guaranteed at this point)
    dq += 0.25 if snapshot.estimates else 0.0
    dq += 0.15 * min(1.0, len(qs) / 8.0)
    dq += 0.10 if rs_sector_3m is not None else 0.0
    dq += 0.10 if snapshot.news else 0.0
    dq += 0.05 if snapshot.short_interest else 0.0
    if snapshot.liquidity.price_staleness_days > 3:
        warnings.append(
            f"price data stale by {snapshot.liquidity.price_staleness_days} trading days"
        )
        dq *= 0.85

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
