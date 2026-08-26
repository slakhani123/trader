"""Deterministic news-narrative sentiment engine.

This engine AGGREGATES provider/lexicon-supplied sentiment values on
``snapshot.news`` — it never scores text itself. Every item is given an
effective weight:

    weight = source-type weight x novelty x time decay

where the source-type weights are factual_event 1.0, analyst_opinion 0.7,
market_commentary 0.5, management_claim 0.4, social 0.15; novelty is the
provider's 0-1 novelty flag; and time decay is a half-life of 21 days
between publication and ``as_of`` (an item 21 days old counts half as much
as one published today). Items dated after ``as_of`` are ignored.

Measurements (all deterministic):

* **direction** — the weighted mean of item sentiments, in -1..+1.
* **rate of change** — mean sentiment of the last 30 days minus the mean
  of the prior 60 days (days 31-90), both weighted by source type x
  novelty only (no double-counting of recency inside the windows).
* **disagreement** — the weighted standard deviation of sentiments
  (needs >= 3 items) plus half of the absolute gap between the
  management-claim mean and the analyst-opinion mean over the last
  90 days (only when both camps are present).
* **price confirmation** — the sign of direction versus the 1-month
  price return: ``confirmed`` when they agree (|direction| >= 0.1 and
  |return| >= 1%), ``contradicted`` when they oppose, ``unconfirmed``
  when either is too small or not computable.

Score construction (plain English)
----------------------------------
Four components, each 0-10 with 5 = neutral, blended with these weights
(a component that cannot be computed is dropped and the remaining weights
renormalise; the components dict then shows a neutral 5.0 for it):

* ``direction`` (40%) — weighted mean sentiment mapped linearly from
  -1..+1 onto 0..10.
* ``momentum`` (25%) — the 30d-vs-prior-60d rate of change mapped from
  -0.6..+0.6 onto 0..10.
* ``confirmation`` (20%) — 8.5 when a positive narrative is confirmed by
  the 1-month return, 2.5 when it is contradicted; 1.5 when a negative
  narrative is confirmed by a falling price (narrative breakdown), 6.0
  when the price resists a negative narrative; 5.0 when unconfirmed.
* ``agreement`` (15%) — disagreement mapped inversely (0.0 -> 10,
  >= 0.9 -> 0). In the blend this component is applied SIGNED by the
  narrative direction: consensus around a positive narrative raises the
  score, consensus around a negative narrative lowers it, and near-zero
  direction neutralises it. The components dict reports the unsigned
  consensus gauge.

Adjustments applied to the blend, in order:

1. **Conviction shrink** — the score is pulled toward 5 when the total
   decay-weighted news volume is thin: multiplier = min(1, weighted
   volume / 3.0). One stale social post cannot move the dial.
2. **Contrarian flag** — direction < -0.4 with the rate of change
   turning up (> +0.05) sets ``contrarian_candidate`` and adds +0.5
   (never more; balance-sheet survivability must come from the quality
   engine via signal rules).
3. **Social cap** — when social items carry more than 40% of total
   weight the score is capped at 6.0 and a warning is added.

The final score is clamped to [0, 10]. Score 5 = neutral. The engine
abstains when the snapshot carries no news at all (score None,
data_quality 0), or when no item is usable (all future-dated or
zero-weighted).
"""

from __future__ import annotations

import math
from datetime import date

from vigil.config import Settings
from vigil.engines.base import abstain, derived_ref, ev, price_ref
from vigil.indicators import ta
from vigil.indicators.stats import clamp, scale_linear
from vigil.schemas.core import (
    Direction,
    EngineResult,
    Evidence,
    InstrumentSnapshot,
    NewsRecord,
    SourceRef,
)

_ENGINE = "sentiment"
_HALF_LIFE_DAYS = 21.0
_RECENT_WINDOW_DAYS = 30
_PRIOR_WINDOW_DAYS = 90  # prior window = days 31..90
_DIVERGENCE_WINDOW_DAYS = 90
_MIN_ITEMS_FOR_DISAGREEMENT = 3
_DIRECTION_SIGN_THRESHOLD = 0.10
_RETURN_SIGN_THRESHOLD = 0.01
_SOCIAL_SHARE_LIMIT = 0.40
_SOCIAL_CAP = 6.0
_CONTRARIAN_DIRECTION = -0.40
_CONTRARIAN_TURN = 0.05
_CONTRARIAN_BOOST = 0.5
_VOLUME_FULL_CONVICTION = 3.0

_TYPE_WEIGHTS: dict[str, float] = {
    "factual_event": 1.0,
    "analyst_opinion": 0.7,
    "market_commentary": 0.5,
    "management_claim": 0.4,
    "social": 0.15,
}

_BLEND_WEIGHTS: dict[str, float] = {
    "direction": 0.40,
    "momentum": 0.25,
    "confirmation": 0.20,
    "agreement": 0.15,
}


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def _decay(age_days: float) -> float:
    return float(0.5 ** (age_days / _HALF_LIFE_DAYS))


def _item_age_days(item: NewsRecord, as_of: date) -> int:
    return (as_of - item.published_at.date()).days


def _weighted_mean(pairs: list[tuple[float, float]]) -> float | None:
    """Mean of values from (weight, value) pairs; None when weightless."""
    total = sum(w for w, _ in pairs)
    if total <= 1e-12:
        return None
    return sum(w * v for w, v in pairs) / total


def _weighted_std(pairs: list[tuple[float, float]]) -> float | None:
    mean = _weighted_mean(pairs)
    if mean is None or len(pairs) < _MIN_ITEMS_FOR_DISAGREEMENT:
        return None
    total = sum(w for w, _ in pairs)
    var = sum(w * (v - mean) ** 2 for w, v in pairs) / total
    return math.sqrt(max(0.0, var))


def _direction_label(value: float | None, hi: float, lo: float) -> Direction:
    if value is None:
        return "neutral"
    if value >= hi:
        return "supports"
    if value <= lo:
        return "contradicts"
    return "neutral"


def _round(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


def analyse(snapshot: InstrumentSnapshot, settings: Settings) -> EngineResult:
    """Aggregate provider-supplied news sentiment (see module docstring)."""
    if not snapshot.news:
        return abstain(_ENGINE, "no news coverage in snapshot — sentiment unmeasurable", 0.0)

    as_of = snapshot.as_of
    warnings: list[str] = []
    evidence: list[Evidence] = []

    # ---- effective item weights ---------------------------------------------
    skipped_future = 0
    items: list[tuple[NewsRecord, int, float]] = []  # (record, age_days, weight)
    for n in snapshot.news:
        age = _item_age_days(n, as_of)
        if age < 0:
            skipped_future += 1
            continue
        weight = _TYPE_WEIGHTS.get(n.source_type, 0.5) * n.novelty * _decay(age)
        items.append((n, age, weight))
    if skipped_future:
        warnings.append(f"{skipped_future} news item(s) dated after as_of ignored")

    weighted_volume = sum(w for _, _, w in items)
    if not items or weighted_volume <= 1e-9:
        return abstain(
            _ENGINE,
            "no usable news items (all future-dated or zero effective weight)",
            data_quality=0.1,
        )

    def add(
        key: str,
        statement: str,
        value: float | str | None,
        direction: Direction,
        source: SourceRef,
    ) -> None:
        evidence.append(ev(snapshot, key, statement, value, direction, "sentiment", source))

    newest = min(items, key=lambda t: (t[1], t[0].record_id))
    newest_src = newest[0].source

    def dref(formula: str) -> SourceRef:
        return derived_ref(snapshot, formula, based_on=newest_src)

    item_count = len(items)

    # ---- direction: decay-weighted mean sentiment -----------------------------
    full_pairs = [(w, n.sentiment) for n, _, w in items]
    direction = _weighted_mean(full_pairs)
    if direction is None:  # unreachable given the weight guard, kept for safety
        return abstain(_ENGINE, "news weights degenerate — direction not computable", 0.1)
    direction_comp = scale_linear(direction, -1.0, 1.0)
    add(
        "news_direction",
        f"Weighted news sentiment is {direction:+.2f} across {item_count} item(s) "
        f"(source-type x novelty x 21d half-life decay)",
        round(direction, 3),
        _direction_label(direction, 0.15, -0.15),
        dref("news_direction_weighted"),
    )

    # ---- rate of change: last 30d vs prior 60d --------------------------------
    recent_pairs = [
        (_TYPE_WEIGHTS.get(n.source_type, 0.5) * n.novelty, n.sentiment)
        for n, age, _ in items
        if age <= _RECENT_WINDOW_DAYS
    ]
    prior_pairs = [
        (_TYPE_WEIGHTS.get(n.source_type, 0.5) * n.novelty, n.sentiment)
        for n, age, _ in items
        if _RECENT_WINDOW_DAYS < age <= _PRIOR_WINDOW_DAYS
    ]
    recent_mean = _weighted_mean(recent_pairs)
    prior_mean = _weighted_mean(prior_pairs)
    rate_of_change: float | None = None
    if recent_mean is not None and prior_mean is not None:
        rate_of_change = recent_mean - prior_mean
        add(
            "news_rate_of_change",
            f"News sentiment moved {rate_of_change:+.2f}: last 30d mean {recent_mean:+.2f} "
            f"vs prior-60d mean {prior_mean:+.2f}",
            round(rate_of_change, 3),
            _direction_label(rate_of_change, 0.15, -0.15),
            dref("news_rate_of_change_30v60"),
        )
    else:
        warnings.append(
            "rate of change not computable — need news in both the last 30 days "
            "and the prior 60 days"
        )
    momentum_comp = (
        scale_linear(rate_of_change, -0.6, 0.6) if rate_of_change is not None else None
    )

    # ---- disagreement: weighted std + management-vs-analyst divergence --------
    weighted_std = _weighted_std(full_pairs)
    mgmt_pairs = [
        (w, n.sentiment)
        for n, age, w in items
        if n.source_type == "management_claim" and age <= _DIVERGENCE_WINDOW_DAYS
    ]
    analyst_pairs = [
        (w, n.sentiment)
        for n, age, w in items
        if n.source_type == "analyst_opinion" and age <= _DIVERGENCE_WINDOW_DAYS
    ]
    mgmt_mean = _weighted_mean(mgmt_pairs)
    analyst_mean = _weighted_mean(analyst_pairs)
    divergence: float | None = None
    if mgmt_mean is not None and analyst_mean is not None:
        divergence = abs(mgmt_mean - analyst_mean)
    disagreement: float | None = None
    if weighted_std is not None:
        disagreement = weighted_std + 0.5 * (divergence or 0.0)
        add(
            "news_disagreement",
            f"Narrative disagreement is {disagreement:.2f} "
            f"(weighted sentiment dispersion {weighted_std:.2f}"
            + (
                f", management-vs-analyst gap {divergence:.2f})"
                if divergence is not None
                else ")"
            ),
            round(disagreement, 3),
            "contradicts" if disagreement >= 0.6 else "neutral",
            dref("news_disagreement_weighted"),
        )
    else:
        warnings.append(
            f"disagreement not computable — fewer than {_MIN_ITEMS_FOR_DISAGREEMENT} news items"
        )
    if divergence is not None and divergence >= 0.6 and mgmt_mean is not None:
        add(
            "mgmt_analyst_divergence",
            f"Management claims average {mgmt_mean:+.2f} while analyst opinion averages "
            f"{analyst_mean:+.2f} over the last {_DIVERGENCE_WINDOW_DAYS} days",
            round(divergence, 3),
            "contradicts",
            dref("mgmt_vs_analyst_divergence_90d"),
        )
    agreement_comp = (
        scale_linear(disagreement, 0.9, 0.0) if disagreement is not None else None
    )

    # ---- price confirmation ----------------------------------------------------
    adj = snapshot.prices["adj_close"].dropna() if not snapshot.prices.empty else None
    m1 = ta.momentum(adj, 21) if adj is not None else None
    if m1 is None:
        warnings.append("1-month price return not computable — narrative unconfirmed by price")
    price_confirms = "unconfirmed"
    if (
        m1 is not None
        and abs(direction) >= _DIRECTION_SIGN_THRESHOLD
        and abs(m1) >= _RETURN_SIGN_THRESHOLD
    ):
        price_confirms = "confirmed" if (direction > 0) == (m1 > 0) else "contradicted"
    positive_narrative = direction >= _DIRECTION_SIGN_THRESHOLD
    if price_confirms == "confirmed":
        confirmation_comp = 8.5 if positive_narrative else 1.5
    elif price_confirms == "contradicted":
        confirmation_comp = 2.5 if positive_narrative else 6.0
    else:
        confirmation_comp = 5.0
    if m1 is not None:
        add(
            "price_confirmation",
            f"1-month price return of {m1 * 100:+.1f}% leaves the "
            f"{direction:+.2f} narrative {price_confirms}",
            round(m1 * 100, 1),
            _direction_label(confirmation_comp, 6.5, 3.5),
            derived_ref(snapshot, "price_confirmation_1m", based_on=price_ref(snapshot)),
        )

    # ---- weighted volume + share by type ----------------------------------------
    share_by_type = {
        t: round(sum(w for n, _, w in items if n.source_type == t) / weighted_volume, 3)
        for t in _TYPE_WEIGHTS
    }
    add(
        "news_weighted_volume",
        f"Decay-weighted news volume is {weighted_volume:.2f} across {item_count} item(s)",
        round(weighted_volume, 3),
        "neutral",
        dref("news_weighted_volume"),
    )

    # Most influential single item, quoted with its own source.
    top_item, top_age, _top_w = max(
        items, key=lambda t: (t[2] * abs(t[0].sentiment), t[0].record_id)
    )
    if abs(top_item.sentiment) > 0:
        add(
            "most_influential_item",
            f'Most influential item: "{top_item.headline}" '
            f"({top_item.source_type}, sentiment {top_item.sentiment:+.2f}, {top_age}d old)",
            round(top_item.sentiment, 2),
            _direction_label(top_item.sentiment, 0.25, -0.25),
            top_item.source,
        )

    # ---- blend --------------------------------------------------------------------
    if direction > _DIRECTION_SIGN_THRESHOLD:
        dsign = 1.0
    elif direction < -_DIRECTION_SIGN_THRESHOLD:
        dsign = -1.0
    else:
        dsign = 0.0
    agreement_signed = (
        5.0 + (agreement_comp - 5.0) * dsign if agreement_comp is not None else None
    )
    comp_raw: dict[str, float | None] = {
        "direction": direction_comp,
        "momentum": momentum_comp,
        "confirmation": confirmation_comp,
        "agreement": agreement_comp,
    }
    blend_values: dict[str, float | None] = dict(comp_raw)
    blend_values["agreement"] = agreement_signed
    total = weight_sum = 0.0
    for name, w in _BLEND_WEIGHTS.items():
        v = blend_values[name]
        if v is None:
            continue
        total += w * v
        weight_sum += w
    score = total / weight_sum  # direction & confirmation always present
    uncomputed = sorted(k for k, v in comp_raw.items() if v is None)
    if uncomputed:
        warnings.append(
            "components not computable from available data (shown neutral, zero weight): "
            + ", ".join(uncomputed)
        )

    # 1. conviction shrink on thin flow
    conviction = min(1.0, weighted_volume / _VOLUME_FULL_CONVICTION)
    score = 5.0 + (score - 5.0) * conviction
    if conviction < 1.0:
        warnings.append(
            f"thin news flow (weighted volume {weighted_volume:.2f} < "
            f"{_VOLUME_FULL_CONVICTION:.0f}) — score shrunk toward neutral"
        )

    # 2. contrarian flag (+0.5 at most; survivability is the quality engine's job)
    contrarian_candidate = bool(
        direction < _CONTRARIAN_DIRECTION
        and rate_of_change is not None
        and rate_of_change > _CONTRARIAN_TURN
    )
    if contrarian_candidate:
        score += _CONTRARIAN_BOOST
        add(
            "contrarian_candidate",
            f"Deeply negative narrative ({direction:+.2f}) with sentiment turning up "
            f"({rate_of_change:+.2f}) — contrarian candidate (survivability not assessed here)",
            round(rate_of_change or 0.0, 3),
            "supports",
            dref("contrarian_turn"),
        )

    # 3. social-heavy cap
    social_share = share_by_type["social"]
    if social_share > _SOCIAL_SHARE_LIMIT:
        warnings.append(
            f"social sources carry {social_share * 100:.0f}% of news weight — score capped "
            f"at {_SOCIAL_CAP:.0f}"
        )
        add(
            "social_heavy_flow",
            f"Social posts carry {social_share * 100:.0f}% of weighted news flow — "
            "narrative reliability reduced",
            round(social_share * 100, 1),
            "contradicts",
            dref("social_share_of_weight"),
        )
        score = min(score, _SOCIAL_CAP)

    # ---- details -------------------------------------------------------------------
    details = {
        "direction_score": round(direction, 3),
        "rate_of_change": _round(rate_of_change),
        "disagreement": _round(disagreement),
        "price_confirms": price_confirms,
        "share_by_type": share_by_type,
        "contrarian_candidate": contrarian_candidate,
        "item_count": item_count,
        "weighted_volume": round(weighted_volume, 3),
    }

    # ---- data quality ----------------------------------------------------------------
    newest_age = newest[1]
    dq = 0.35
    dq += 0.35 * min(1.0, item_count / 8.0)
    dq += 0.15 * max(0.0, 1.0 - newest_age / 45.0)
    dq += 0.15 if m1 is not None else 0.0
    if newest_age > 60:
        warnings.append(f"newest news item is {newest_age} days old — coverage is stale")

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
