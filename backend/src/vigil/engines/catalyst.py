"""Forward catalyst-calendar engine.

Score construction (plain English)
----------------------------------
This engine scores the *opportunity* embedded in the visible event calendar:
how dense, near, relevant and credible the unresolved catalysts are, plus a
small tail-wind (or head-wind) from recently resolved outcomes. 5 is neutral:
an empty calendar with normal data coverage scores exactly 5. The engine
abstains only when the snapshot carries no catalyst records AND no news AND
news coverage itself is flagged missing (``'news' in snapshot.quality.missing``)
— in that case an empty calendar cannot be distinguished from missing coverage.

Four components, each 0-10 starting from a 5.0 neutral baseline, blended into
the engine score with fixed weights:

* ``near_term`` (35%) — unresolved catalysts due within ~1 month,
* ``medium_term`` (35%) — due within ~6 months (beyond the near window),
* ``long_term`` (10%) — due beyond 6 months out to the long horizon,
* ``recent_outcomes`` (20%) — resolved catalysts from the last 90 days.

Horizon windows come from ``settings.horizons``: each trading-day maximum is
converted to calendar days by multiplying by 7/5 (defaults: 28 / 176 / 1764
calendar days).

Each unresolved upcoming catalyst adds
``relevance x date-confidence x proximity x binary factor x priced-in factor
x bucket gain`` points on top of its bucket's neutral 5.0 (bucket clamped to
[0, 10]):

* relevance by kind — earnings 0.8, guidance 0.9, regulatory 0.9, m_and_a 1.0,
  capital_return 0.7, contract 0.6, product_launch 0.5, investor_day 0.4,
  index_change 0.5, management_change 0.5, filing 0.3; refinancing is 1.0 when
  leverage matters (net debt > 3x TTM EBIT, TTM EBIT <= 0 with debt
  outstanding, or debt due within 1 year exceeding cash), 0.5 when it clearly
  does not, and 0.75 (with a warning) when fundamentals cannot settle it;
* date confidence — 1.0 when ``date_confirmed``, 0.7 for a tentative date;
* proximity — decays linearly from 1.0 at the start of the bucket's window to
  0.4 at its end (nearer events count more);
* binary factor — a binary event within 20 calendar days contributes 0 to the
  opportunity AND subtracts 0.75 from ``near_term`` (capped at -1.5 in total,
  with contradicting evidence): a near coin-flip is risk, not opportunity.
  Binary events further out contribute at 0.4x. ``binary_event_within_20d``
  and ``next_binary`` are emitted so confidence gating can act downstream;
* priced-in factor — a labelled deterministic heuristic, never a probability:
  run-up = 20-trading-day return minus the benchmark's. When the excess
  run-up exceeds +10% every upcoming catalyst's contribution is scaled down
  linearly, reaching zero (fully priced in) at +25%. Because an unresolved
  catalyst's direction is unobservable, a positive run-up discounts the whole
  forward calendar rather than individual "positive" events;
* bucket gains — near 2.2, medium 1.8, long 1.2 points per full-weight event.

``recent_outcomes`` starts at 5.0 and adds (or subtracts)
``relevance x age-decay x 1.5`` per resolved catalyst of the last 90 days
whose outcome text classifies deterministically as positive or negative:
first the "EPS surprise +/-x%" pattern (sign of the surprise), otherwise a
fixed word-boundary keyword lexicon (raised/beat/approved/... vs
cut/missed/rejected/warning/...); unclassifiable outcomes are neutral. Age
decay runs linearly from 1.0 (today) to 0.5 (90 days old); the net
adjustment is capped at +/-3.0.

Engine score = 0.35 x near_term + 0.35 x medium_term + 0.10 x long_term +
0.20 x recent_outcomes, clamped to [0, 10].
"""

from __future__ import annotations

import re
from typing import Any

from vigil.config import Settings
from vigil.engines.base import abstain, derived_ref, ev, price_ref
from vigil.indicators import ta
from vigil.indicators.stats import clamp, scale_linear
from vigil.schemas.core import (
    CatalystRecord,
    Direction,
    EngineResult,
    Evidence,
    InstrumentSnapshot,
    SourceRef,
)

_ENGINE = "catalyst"

_WEIGHTS: dict[str, float] = {
    "near_term": 0.35,
    "medium_term": 0.35,
    "long_term": 0.10,
    "recent_outcomes": 0.20,
}

_RELEVANCE: dict[str, float] = {
    "earnings": 0.8,
    "guidance": 0.9,
    "regulatory": 0.9,
    "m_and_a": 1.0,
    "capital_return": 0.7,
    "contract": 0.6,
    "product_launch": 0.5,
    "investor_day": 0.4,
    "index_change": 0.5,
    "management_change": 0.5,
    "filing": 0.3,
}
_REFI_LEVERED = 1.0
_REFI_UNLEVERED = 0.5
_REFI_UNKNOWN = 0.75

_CONFIRMED_CONF = 1.0
_TENTATIVE_CONF = 0.7
_GAINS: dict[str, float] = {"near_term": 2.2, "medium_term": 1.8, "long_term": 1.2}
_PROX_FLOOR = 0.4

_BINARY_NEAR_DAYS = 20
_BINARY_FAR_FACTOR = 0.4
_BINARY_NEAR_PENALTY = 0.75
_BINARY_NEAR_PENALTY_CAP = 1.5

_PRICED_IN_START = 0.10  # excess 20d run-up where discounting begins
_PRICED_IN_FULL = 0.25  # excess run-up treated as fully priced in

_OUTCOME_WINDOW_DAYS = 90
_OUTCOME_GAIN = 1.5
_OUTCOME_CAP = 3.0
_MAX_OUTCOME_EVIDENCE = 2
_MAX_UPCOMING_EVIDENCE = 3

_NET_DEBT_EBIT_LIMIT = 3.0

_SURPRISE_RE = re.compile(r"EPS surprise\s*([+-]?\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
_POSITIVE_RE = re.compile(
    r"\b(raised?|raises|beat[s]?|above|approved|approval|clear(?:ed|s)|complete[sd]?|"
    r"completed|won|wins?|positive|ahead|exceed(?:s|ed)?|up|successful|awarded)\b",
    re.IGNORECASE,
)
_NEGATIVE_RE = re.compile(
    r"\b(cut[s]?|lower(?:ed|s)?|down|miss(?:ed|es)?|below|rejected|delay(?:ed|s)?|"
    r"withdrawn|negative|fail(?:ed|s)?|warning|downgrade[sd]?|short of|suspended)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def _classify_outcome(outcome: str) -> int:
    """Deterministic sign of a resolved outcome: +1 / -1 / 0 (unclassifiable).

    The "EPS surprise +/-x%" pattern wins; otherwise a fixed keyword lexicon.
    """
    m = _SURPRISE_RE.search(outcome)
    if m is not None:
        surprise = float(m.group(1))
        if surprise > 0.5:
            return 1
        if surprise < -0.5:
            return -1
        return 0
    pos = _POSITIVE_RE.search(outcome) is not None
    neg = _NEGATIVE_RE.search(outcome) is not None
    if pos and not neg:
        return 1
    if neg and not pos:
        return -1
    return 0


def _leverage_matters(snapshot: InstrumentSnapshot) -> bool | None:
    """Whether refinancing risk is material. None = cannot tell from data."""
    latest = snapshot.latest_fundamental()
    if latest is None or latest.total_debt is None:
        return None
    debt = latest.total_debt
    cash = latest.cash_and_equivalents
    wall = (
        latest.debt_due_within_1y is not None
        and cash is not None
        and latest.debt_due_within_1y > cash
    )
    net_debt = debt - (cash if cash is not None else 0.0)
    ebit_ttm = snapshot.ttm_sum("operating_income")
    if ebit_ttm is not None and ebit_ttm > 0:
        return bool(net_debt / ebit_ttm > _NET_DEBT_EBIT_LIMIT or wall)
    if ebit_ttm is not None and ebit_ttm <= 0 and debt > 0:
        return True
    return True if wall else None


def _excess_runup_20d(snapshot: InstrumentSnapshot) -> float | None:
    """20-trading-day return minus the benchmark's, for the priced-in heuristic."""
    if snapshot.prices.empty or "adj_close" not in snapshot.prices.columns:
        return None
    own = ta.momentum(snapshot.prices["adj_close"].dropna(), 20)
    if own is None:
        return None
    bench = snapshot.benchmark
    if bench is None or bench.dropna().empty:
        return None
    market = ta.momentum(bench.dropna(), 20)
    if market is None:
        return None
    return own - market


def _relevance(kind: str, leverage: bool | None) -> float:
    if kind == "refinancing":
        if leverage is None:
            return _REFI_UNKNOWN
        return _REFI_LEVERED if leverage else _REFI_UNLEVERED
    return _RELEVANCE.get(kind, 0.5)


def _bucket(days: int, windows: dict[str, int]) -> tuple[str, int, int]:
    """(bucket name, window start, window end) for a days-until value."""
    if days <= windows["near"]:
        return "near_term", 0, windows["near"]
    if days <= windows["medium"]:
        return "medium_term", windows["near"], windows["medium"]
    return "long_term", windows["medium"], windows["long"]


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


def analyse(snapshot: InstrumentSnapshot, settings: Settings) -> EngineResult:
    """Forward event analysis over ``snapshot.catalysts`` (see module docstring)."""
    if not snapshot.catalysts and not snapshot.news and "news" in snapshot.quality.missing:
        return abstain(
            _ENGINE,
            "no catalyst records and no news coverage — cannot distinguish an empty "
            "calendar from missing coverage",
            data_quality=0.0,
        )

    as_of = snapshot.as_of
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
        evidence.append(ev(snapshot, key, statement, value, direction, "catalysts", source))

    windows = {
        "near": round(settings.horizons.short_max_days * 7 / 5),
        "medium": round(settings.horizons.medium_max_days * 7 / 5),
        "long": round(settings.horizons.long_max_days * 7 / 5),
    }

    # ---- priced-in heuristic (instrument-level, documented) -----------------
    runup = _excess_runup_20d(snapshot)
    priced_in_pct: float | None
    if runup is None:
        priced_in_pct = None
        warnings.append("20-day excess run-up not computable — priced-in heuristic skipped")
    else:
        priced_in_pct = scale_linear(runup, _PRICED_IN_START, _PRICED_IN_FULL, 0.0, 100.0)
    priced_in_mult = 1.0 - (priced_in_pct / 100.0 if priced_in_pct is not None else 0.0)

    # ---- refinancing relevance context --------------------------------------
    leverage = _leverage_matters(snapshot)
    has_refi = any(
        c.kind == "refinancing" and not c.resolved for c in snapshot.catalysts
    )
    if has_refi and leverage is None:
        warnings.append(
            "leverage not assessable from fundamentals — refinancing catalyst weighted at "
            f"{_REFI_UNKNOWN:.2f}"
        )

    # ---- upcoming (unresolved) catalysts ------------------------------------
    sums = {"near_term": 0.0, "medium_term": 0.0, "long_term": 0.0}
    near_binary_penalty = 0.0
    upcoming: list[dict[str, Any]] = []
    scored: list[tuple[float, int, CatalystRecord]] = []  # (contribution, days, record)
    overdue = 0

    ordered = sorted(
        snapshot.catalysts, key=lambda c: (c.expected_date, c.kind, c.record_id)
    )
    for c in ordered:
        if c.resolved:
            continue
        days = (c.expected_date - as_of).days
        if days < 0:
            overdue += 1
            continue
        if days > windows["long"]:
            continue
        bucket, lo, hi = _bucket(days, windows)
        frac = (days - lo) / (hi - lo) if hi > lo else 0.0
        proximity = 1.0 - (1.0 - _PROX_FLOOR) * frac
        confidence = _CONFIRMED_CONF if c.date_confirmed else _TENTATIVE_CONF
        relevance = _relevance(c.kind, leverage)
        if c.binary and days <= _BINARY_NEAR_DAYS:
            binary_factor = 0.0
            near_binary_penalty = min(
                _BINARY_NEAR_PENALTY_CAP, near_binary_penalty + _BINARY_NEAR_PENALTY
            )
        elif c.binary:
            binary_factor = _BINARY_FAR_FACTOR
        else:
            binary_factor = 1.0
        contribution = (
            relevance * confidence * proximity * binary_factor * priced_in_mult
            * _GAINS[bucket]
        )
        sums[bucket] += contribution
        scored.append((contribution, days, c))
        upcoming.append(
            {
                "kind": c.kind,
                "date": c.expected_date.isoformat(),
                "days": days,
                "binary": bool(c.binary),
                "confirmed": bool(c.date_confirmed),
                "description": c.description,
                "priced_in_pct": round(priced_in_pct, 1) if priced_in_pct is not None else None,
                "relevance": round(relevance, 2),
            }
        )

    if overdue:
        warnings.append(
            f"{overdue} unresolved catalyst(s) past their expected date — excluded from "
            "the forward calendar"
        )

    # ---- recent resolved outcomes --------------------------------------------
    outcome_net = 0.0
    classified: list[tuple[int, int, CatalystRecord]] = []  # (age_days, sign, record)
    for c in ordered:
        if not c.resolved or not c.outcome:
            continue
        when = c.outcome_date or c.expected_date
        age = (as_of - when).days
        if not 0 <= age <= _OUTCOME_WINDOW_DAYS:
            continue
        sign = _classify_outcome(c.outcome)
        if sign == 0:
            continue
        decay = 1.0 - (age / _OUTCOME_WINDOW_DAYS) * 0.5
        outcome_net += sign * _relevance(c.kind, leverage) * decay * _OUTCOME_GAIN
        classified.append((age, sign, c))
    outcome_net = max(-_OUTCOME_CAP, min(_OUTCOME_CAP, outcome_net))

    # ---- components & score ----------------------------------------------------
    components = {
        "near_term": clamp(5.0 + sums["near_term"] - near_binary_penalty),
        "medium_term": clamp(5.0 + sums["medium_term"]),
        "long_term": clamp(5.0 + sums["long_term"]),
        "recent_outcomes": clamp(5.0 + outcome_net),
    }
    score = clamp(sum(_WEIGHTS[name] * value for name, value in components.items()))

    # ---- flags for downstream gating ---------------------------------------------
    binaries = [(days, c) for _, days, c in scored if c.binary]
    next_binary: dict[str, Any] | None = None
    next_binary_rec: CatalystRecord | None = None
    if binaries:
        days_b, next_binary_rec = min(binaries, key=lambda t: (t[0], t[1].record_id))
        next_binary = {
            "kind": next_binary_rec.kind,
            "date": next_binary_rec.expected_date.isoformat(),
            "days": days_b,
        }
    binary_event_within_20d = bool(
        next_binary is not None and int(next_binary["days"]) <= _BINARY_NEAR_DAYS
    )
    earnings_up = [(days, c) for _, days, c in scored if c.kind == "earnings"]
    next_earnings: dict[str, Any] | None = None
    next_earnings_rec: CatalystRecord | None = None
    if earnings_up:
        days_e, next_earnings_rec = min(earnings_up, key=lambda t: (t[0], t[1].record_id))
        next_earnings = {
            "date": next_earnings_rec.expected_date.isoformat(),
            "days": days_e,
            "confirmed": bool(next_earnings_rec.date_confirmed),
        }

    # ---- evidence -------------------------------------------------------------------
    nearest_src = scored[0][2].source if scored else pref
    n_medium = sum(1 for u in upcoming if u["days"] <= windows["medium"])
    n_near = sum(1 for u in upcoming if u["days"] <= windows["near"])
    if upcoming:
        density_dir: Direction = (
            "supports" if sums["near_term"] + sums["medium_term"] > 0.5 else "neutral"
        )
        add(
            "catalyst_calendar_density",
            f"{n_medium} unresolved catalyst(s) within {windows['medium']} days "
            f"({n_near} within {windows['near']} days)",
            float(n_medium),
            density_dir,
            derived_ref(snapshot, "catalyst_calendar_density", based_on=nearest_src),
        )
    else:
        add(
            "catalyst_calendar_empty",
            "No unresolved catalysts on the visible forward calendar",
            0.0,
            "neutral",
            derived_ref(snapshot, "catalyst_calendar_density", based_on=pref),
        )
        warnings.append("catalyst calendar is empty — calendar components held at neutral")

    if next_earnings is not None and next_earnings_rec is not None:
        label = "date confirmed" if next_earnings["confirmed"] else "date tentative"
        add(
            "next_earnings",
            f"Next earnings event expected {next_earnings['date']} in "
            f"{next_earnings['days']} day(s) ({label})",
            float(next_earnings["days"]),
            "neutral",
            next_earnings_rec.source,
        )

    top = sorted(scored, key=lambda t: (-t[0], t[1], t[2].record_id))[:_MAX_UPCOMING_EVIDENCE]
    for contribution, days, c in top:
        label = "date confirmed" if c.date_confirmed else "date tentative"
        direction: Direction = "supports" if contribution >= 0.3 else "neutral"
        add(
            f"upcoming_{c.kind}_{days}d",
            f"Upcoming {c.kind}: {c.description} expected {c.expected_date.isoformat()} "
            f"({days}d away, {label})",
            float(days),
            direction,
            c.source,
        )

    if next_binary is not None and next_binary_rec is not None:
        add(
            "binary_event_proximity",
            f"Binary {next_binary['kind']} event in {next_binary['days']} calendar day(s) "
            f"({next_binary['date']}) — outcome dominates the stock either way",
            float(next_binary["days"]),
            "contradicts",
            next_binary_rec.source,
        )
        if binary_event_within_20d:
            warnings.append(
                f"binary {next_binary['kind']} event within {_BINARY_NEAR_DAYS} days — "
                "confidence gating should apply"
            )

    if runup is not None and priced_in_pct is not None and priced_in_pct >= 1.0 and upcoming:
        add(
            "catalyst_priced_in",
            f"Priced-in heuristic: 20-day excess run-up of {runup * 100:+.1f}% vs benchmark "
            f"scales upcoming catalyst credit down by {priced_in_pct:.0f}%",
            round(priced_in_pct, 1),
            "contradicts",
            derived_ref(snapshot, "catalyst_priced_in_runup_20d", based_on=pref),
        )

    classified.sort(key=lambda t: (t[0], t[2].record_id))
    for age, sign, c in classified[:_MAX_OUTCOME_EVIDENCE]:
        when = c.outcome_date or c.expected_date
        add(
            f"recent_outcome_{c.kind}_{age}d",
            f'Resolved {c.kind} on {when.isoformat()}: "{c.outcome}" — classified '
            f"{'positive' if sign > 0 else 'negative'}",
            c.outcome,
            "supports" if sign > 0 else "contradicts",
            c.source,
        )

    # ---- details ---------------------------------------------------------------------
    binary_event_within = (
        {"days": next_binary["days"], "kind": next_binary["kind"]}
        if next_binary is not None
        else None
    )
    details: dict[str, Any] = {
        "upcoming": upcoming,
        "binary_event_within_20d": binary_event_within_20d,
        "binary_event_within": binary_event_within,
        "next_binary": next_binary,
        "next_earnings": next_earnings,
        "windows_days": {
            "near": windows["near"], "medium": windows["medium"], "long": windows["long"],
        },
        "runup_excess_20d_pct": round(runup * 100, 2) if runup is not None else None,
        "recent_outcome_count": len(classified),
    }

    # ---- data quality -------------------------------------------------------------------
    dq = 0.55 if snapshot.catalysts else 0.25
    dq += 0.20 if runup is not None else 0.0
    confirmed_share = (
        sum(1 for u in upcoming if u["confirmed"]) / len(upcoming) if upcoming else 1.0
    )
    dq += 0.15 * confirmed_share
    dq += 0.10 if snapshot.news else 0.0
    if not snapshot.catalysts:
        warnings.append("no catalyst records visible — scored on news coverage alone")

    return EngineResult(
        engine=_ENGINE,
        score=round(score, 2),
        components={name: round(value, 2) for name, value in components.items()},
        evidence=evidence,
        warnings=warnings,
        data_quality=round(min(1.0, dq), 2),
        details=details,
    )
