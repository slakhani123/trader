"""Signal-family rules.

Each rule is a deterministic predicate over (snapshot, ScoreBundle) that
emits a SignalCandidate when its conditions are met — full trigger or
watch-grade setup. Every rule documents its conditions in ``rationale``
lines so the user can see exactly why a signal fired.

Buy-side rules only fire when the composite gate for their horizon passed;
watch candidates require near-miss conditions; AVOID is informational and
gated only on confidence.
"""

from __future__ import annotations

from typing import Any

from vigil.config import Settings
from vigil.schemas.core import (
    EngineResult,
    EntryPlan,
    Evidence,
    HorizonScore,
    InstrumentSnapshot,
    Scenario,
    ScoreBundle,
    SignalCandidate,
    SignalFamily,
)


def _details(bundle: ScoreBundle, engine: str) -> dict[str, Any]:
    r = bundle.engine_results.get(engine)
    return r.details if r is not None else {}


def _score(bundle: ScoreBundle, engine: str) -> float | None:
    r = bundle.engine_results.get(engine)
    return r.score if r is not None else None


def _comp(bundle: ScoreBundle, horizon: str, key: str) -> float | None:
    return bundle.horizons[horizon].components.get(key)


def _gate_ok(bundle: ScoreBundle, horizon: str) -> bool:
    h = bundle.horizons[horizon]
    return not h.abstained and h.gate is not None and h.gate.passed


def _evidence_split(bundle: ScoreBundle, cap: int = 8) -> tuple[list[Evidence], list[Evidence]]:
    supporting = [e for e in bundle.evidence if e.direction == "supports"][:cap]
    contradicting = [e for e in bundle.evidence if e.direction == "contradicts"][:cap]
    return supporting, contradicting


def _scenarios(bundle: ScoreBundle) -> list[Scenario]:
    out: list[Scenario] = []
    raw = _details(bundle, "valuation").get("scenarios") or {}
    for name in ("base", "bull", "bear"):
        sc = raw.get(name)
        if isinstance(sc, dict) and isinstance(sc.get("price"), int | float):
            out.append(
                Scenario(name=name, price=float(sc["price"]), rationale=str(sc.get("rationale", "")))
            )
    return out


def _zone(d: dict | None) -> tuple[float, float] | None:
    if not d:
        return None
    low, high = d.get("low"), d.get("high")
    if isinstance(low, int | float) and isinstance(high, int | float) and low > 0:
        return float(low), float(high)
    return None


def build_entry_plan(
    family: SignalFamily,
    horizon: str,
    snapshot: InstrumentSnapshot,
    bundle: ScoreBundle,
    watch: bool,
) -> EntryPlan:
    tech = _details(bundle, "technical")
    val = _details(bundle, "valuation")
    qual = _details(bundle, "quality")
    price = snapshot.last_close or 0.0
    atr_pct = tech.get("atr_pct") or 0.03

    def usable(z: tuple[float, float] | None) -> tuple[float, float] | None:
        """An entry zone demanding a >25% fall from here is not an
        accumulation zone — it is a different thesis. Reject it."""
        if z is None or price <= 0:
            return z
        return z if z[1] >= price * 0.75 else None

    zone = None
    if family in (SignalFamily.DEEP_VALUE, SignalFamily.QUALITY_COMPOUNDER):
        zone = usable(_zone(val.get("entry_zone_hint"))) or usable(
            _zone(tech.get("nearest_support"))
        )
    elif family == SignalFamily.BREAKOUT_CONTINUATION:
        rng_high = (tech.get("breakout") or {}).get("range_high")
        if isinstance(rng_high, int | float) and rng_high > 0:
            zone = (float(rng_high) * 0.99, min(price * 1.02, float(rng_high) * 1.06))
        else:
            zone = (price * 0.98, price * 1.02)
    else:
        zone = usable(_zone(tech.get("entry_zone_hint"))) or usable(
            _zone(tech.get("nearest_support"))
        )
    if zone is None and price > 0:
        zone = (price * (1 - 1.5 * atr_pct), price)

    stop = tech.get("stop_hint") if horizon == "short" else None
    scenarios = _scenarios(bundle)
    target_low = target_high = None
    base = next((s.price for s in scenarios if s.name == "base"), None)
    bull = next((s.price for s in scenarios if s.name == "bull"), None)
    if horizon == "short":
        res = tech.get("resistance_levels") or []
        if res:
            target_low = target_high = float(res[0])
            if len(res) > 1:
                target_high = float(res[1])
    if target_low is None and base is not None:
        target_low, target_high = base, (bull or base)

    reward_risk = tech.get("reward_risk")
    if (
        reward_risk is None
        and zone and stop and target_low
        and isinstance(stop, int | float)
        and zone[1] > stop
    ):
        mid = (zone[0] + zone[1]) / 2
        if mid > stop:
            reward_risk = round((target_low - mid) / (mid - stop), 2)

    invalidation: list[str] = []
    fundamental_inv: list[str] = []
    if stop:
        invalidation.append(
            f"Two consecutive daily closes below {stop:.2f} "
            f"(support zone floor minus one ATR)"
        )
    ns = _zone(tech.get("nearest_support"))
    if ns and not stop:
        invalidation.append(
            f"Two consecutive daily closes below the {ns[0]:.2f}–{ns[1]:.2f} support zone"
        )
    if family == SignalFamily.BREAKOUT_CONTINUATION:
        rng_high = (tech.get("breakout") or {}).get("range_high")
        if isinstance(rng_high, int | float):
            invalidation.append(
                f"Close back below the breakout level {float(rng_high):.2f} within 10 sessions "
                "(failed breakout)"
            )
    growth = qual.get("growth_metrics") or {}
    if family in (SignalFamily.QUALITY_COMPOUNDER, SignalFamily.DEEP_VALUE):
        if growth.get("revenue_cagr_3y") is not None:
            fundamental_inv.append("TTM revenue declines for two consecutive quarters")
        fundamental_inv.append("Operating margin falls more than 300bp below its 3-year average")
        fundamental_inv.append("Net-down estimate revisions for two consecutive months")
    if qual.get("refinancing_risk"):
        fundamental_inv.append("Refinancing need remains unresolved within 6 months of maturity")
    if family == SignalFamily.FUNDAMENTAL_INFLECTION:
        fundamental_inv.append("Operating margin falls back below zero in the next reported quarter")
    if family == SignalFamily.ESTIMATE_MOMENTUM:
        fundamental_inv.append("30-day revision breadth turns negative")
    if not fundamental_inv:
        fundamental_inv.append("Reported fundamentals contradict the triggering evidence")

    conditions_before_entry: list[str] = []
    if watch:
        if family == SignalFamily.BREAKOUT_CONTINUATION:
            rng_high = (tech.get("breakout") or {}).get("range_high")
            lvl = f"{float(rng_high):.2f}" if isinstance(rng_high, int | float) else "the range high"
            conditions_before_entry = [
                f"Daily close above {lvl} on at least 1.3× average volume",
                "Relative strength versus the market positive over the following week",
            ]
        elif family == SignalFamily.OVERSOLD_AT_SUPPORT:
            conditions_before_entry = [
                "Two sessions holding the support zone with intraday lows above it",
                "A reversal day (close in the top third of the daily range) on above-average volume",
            ]
        else:
            conditions_before_entry = [
                "Composite opportunity crosses the alert gate with confidence intact",
            ]

    trim = [
        "Price reaches the bull-scenario valuation" if bull else "Price reaches the target range",
        "Technical extension: price stretches >30% above its 50-day average",
        "Expected upside to base scenario falls below half the downside to support",
    ]
    if family in (SignalFamily.BREAKOUT_CONTINUATION, SignalFamily.ESTIMATE_MOMENTUM):
        trim.append("Price rises further while estimate revisions stop confirming")
    exits = [
        "A fundamental invalidation condition above is met",
        "Composite risk rises above the configured maximum while opportunity decays",
        "The original catalyst resolves and is fully reflected in price",
    ]

    return EntryPlan(
        zone_low=round(zone[0], 2) if zone else None,
        zone_high=round(zone[1], 2) if zone else None,
        stop=round(float(stop), 2) if isinstance(stop, int | float) else None,
        conditions_before_entry=conditions_before_entry,
        invalidation_conditions=invalidation,
        fundamental_invalidation=fundamental_inv,
        target_low=round(target_low, 2) if target_low else None,
        target_high=round(target_high, 2) if target_high else None,
        scenarios=scenarios,
        trim_conditions=trim,
        exit_conditions=exits,
        reward_risk=reward_risk if isinstance(reward_risk, int | float) else None,
    )


def _candidate(
    family: SignalFamily,
    horizon: str,
    snapshot: InstrumentSnapshot,
    bundle: ScoreBundle,
    rationale: list[str],
    watch: bool = False,
    thesis_keys: list[str] | None = None,
) -> SignalCandidate:
    supporting, contradicting = _evidence_split(bundle)
    return SignalCandidate(
        family=family,
        horizon=horizon,  # type: ignore[arg-type]
        instrument_id=snapshot.info.instrument_id,
        as_of=bundle.as_of,
        state_hint="WATCHING" if watch else "TRIGGERED",
        scores=bundle.horizons[horizon],
        entry_plan=build_entry_plan(family, horizon, snapshot, bundle, watch),
        thesis_keys=thesis_keys or [e.key for e in supporting[:4]],
        supporting=supporting,
        contradicting=contradicting,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Buy-side family rules
# ---------------------------------------------------------------------------


def rule_deep_value(
    snapshot: InstrumentSnapshot, bundle: ScoreBundle, settings: Settings
) -> SignalCandidate | None:
    val_score = _score(bundle, "valuation")
    qual_score = _score(bundle, "quality")
    if val_score is None or qual_score is None:
        return None
    trap = _details(bundle, "valuation").get("value_trap") or {}
    failed = trap.get("failed_checks") or []
    cat_score = _score(bundle, "catalyst") or 0.0
    horizon = "long" if qual_score >= 6.0 else "medium"
    conds = {
        f"valuation score {val_score:.1f} ≥ 6.5": val_score >= 6.5,
        f"value-trap checks failed: {len(failed)} ≤ 1": len(failed) <= 1,
        f"quality score {qual_score:.1f} ≥ 4.5": qual_score >= 4.5,
        f"catalyst support {cat_score:.1f} ≥ 5.0": cat_score >= 5.0,
        f"{horizon} gate passed": _gate_ok(bundle, horizon),
    }
    rationale = [("✓ " if ok else "✗ ") + c for c, ok in conds.items()]
    if all(conds.values()):
        return _candidate(SignalFamily.DEEP_VALUE, horizon, snapshot, bundle, rationale)
    # Watch: cheap and clean but the catalyst is missing or gate is near-miss.
    near = (
        val_score >= 7.0
        and len(failed) <= 1
        and qual_score >= 4.5
        and bundle.horizons[horizon].confidence >= settings.gates.min_confidence - 1.0
        and bundle.horizons[horizon].opportunity >= settings.gates.min_opportunity - 0.75
    )
    if near:
        return _candidate(
            SignalFamily.DEEP_VALUE, horizon, snapshot, bundle, rationale, watch=True
        )
    return None


def rule_quality_compounder(
    snapshot: InstrumentSnapshot, bundle: ScoreBundle, settings: Settings
) -> SignalCandidate | None:
    q = _comp(bundle, "long", "quality")
    g = _comp(bundle, "long", "growth")
    bs = _comp(bundle, "long", "balance_sheet")
    v = _comp(bundle, "long", "valuation")
    if q is None or v is None:
        return None
    conds = {
        f"quality {q:.1f} ≥ 7.0": q >= 7.0,
        f"growth {g if g is not None else 0:.1f} ≥ 6.0": (g or 0) >= 6.0,
        f"balance sheet {bs if bs is not None else 0:.1f} ≥ 6.0": (bs or 0) >= 6.0,
        f"valuation not stretched: {v:.1f} ≥ 4.5": v >= 4.5,
        "long gate passed": _gate_ok(bundle, "long"),
    }
    rationale = [("✓ " if ok else "✗ ") + c for c, ok in conds.items()]
    if all(conds.values()):
        return _candidate(SignalFamily.QUALITY_COMPOUNDER, "long", snapshot, bundle, rationale)
    return None


def rule_oversold_at_support(
    snapshot: InstrumentSnapshot, bundle: ScoreBundle, settings: Settings
) -> SignalCandidate | None:
    tech = _details(bundle, "technical")
    dd = tech.get("drawdown_from_52w_high_pct")
    ns = _zone(tech.get("nearest_support"))
    rsi = tech.get("rsi14")
    price = snapshot.last_close
    q = _score(bundle, "quality")
    if dd is None or price is None or q is None:
        return None
    near_support = ns is not None and price <= ns[1] * 1.04
    cat = _details(bundle, "catalyst")
    nb = cat.get("next_binary") or {}
    binary_soon = isinstance(nb.get("days"), int | float) and nb["days"] <= 10
    rr = tech.get("reward_risk")
    conds = {
        f"drawdown {dd:.0f}% ≤ −12%": dd <= -12,
        "price at a tested support zone": near_support,
        f"RSI(14) {rsi if rsi is not None else 99:.0f} ≤ 40": (rsi or 99) <= 40,
        f"reward/risk {rr if rr is not None else 0:.1f} ≥ {settings.gates.min_reward_risk}":
            (rr or 0) >= settings.gates.min_reward_risk,
        f"quality backdrop {q:.1f} ≥ 5.5": q >= 5.5,
        "no binary event within 10 days": not binary_soon,
        "short gate passed": _gate_ok(bundle, "short"),
    }
    rationale = [("✓ " if ok else "✗ ") + c for c, ok in conds.items()]
    if all(conds.values()):
        return _candidate(SignalFamily.OVERSOLD_AT_SUPPORT, "short", snapshot, bundle, rationale)
    # Watch when oversold at support but confirmation is missing.
    if dd <= -12 and near_support and (rsi or 99) <= 38 and q >= 5.5 and not binary_soon:
        return _candidate(
            SignalFamily.OVERSOLD_AT_SUPPORT, "short", snapshot, bundle, rationale, watch=True
        )
    return None


def rule_constructive_pullback(
    snapshot: InstrumentSnapshot, bundle: ScoreBundle, settings: Settings
) -> SignalCandidate | None:
    tech = _details(bundle, "technical")
    mom = _details(bundle, "momentum")
    dd = tech.get("drawdown_from_52w_high_pct")
    ns = _zone(tech.get("nearest_support"))
    price = snapshot.last_close
    m_score = _score(bundle, "momentum")
    if dd is None or price is None or m_score is None:
        return None
    rs3 = (mom.get("rs") or {}).get("market_3m")
    conds = {
        "established uptrend": tech.get("trend_state") == "uptrend",
        f"pullback depth {dd:.0f}% in [−18, −4]": -18 <= dd <= -4,
        "support within 5% below price": ns is not None and price <= ns[1] * 1.05,
        "relative strength holding": (rs3 or 0) > -0.02,
        f"momentum {m_score:.1f} ≥ 5.5": m_score >= 5.5,
        "no parabolic penalty": not mom.get("parabolic", False),
        "short gate passed": _gate_ok(bundle, "short"),
    }
    rationale = [("✓ " if ok else "✗ ") + c for c, ok in conds.items()]
    if all(conds.values()):
        return _candidate(SignalFamily.CONSTRUCTIVE_PULLBACK, "short", snapshot, bundle, rationale)
    return None


def rule_breakout(
    snapshot: InstrumentSnapshot, bundle: ScoreBundle, settings: Settings
) -> SignalCandidate | None:
    mom = _details(bundle, "momentum")
    tech = _details(bundle, "technical")
    m_score = _score(bundle, "momentum")
    if m_score is None:
        return None
    state = (tech.get("breakout") or {}).get("state")
    horizon = "medium" if (mom.get("revision_breadth_30d") or 0) >= 0.2 else "short"
    conds = {
        "accumulation → breakout confirmed": bool(mom.get("accumulation_breakout")),
        f"breakout state '{state}' confirmed": state == "breakout",
        f"momentum {m_score:.1f} ≥ 6.5": m_score >= 6.5,
        "not parabolic": not mom.get("parabolic", False),
        f"{horizon} gate passed": _gate_ok(bundle, horizon),
    }
    rationale = [("✓ " if ok else "✗ ") + c for c, ok in conds.items()]
    if all(conds.values()):
        return _candidate(SignalFamily.BREAKOUT_CONTINUATION, horizon, snapshot, bundle, rationale)
    # Watch: coiling consolidation with volume building.
    vol_ratio = (tech.get("breakout") or {}).get("volume_ratio") or 1.0
    if state == "consolidating" and vol_ratio >= 1.15 and m_score >= 5.5 and not mom.get("parabolic"):
        rationale.append(f"✓ consolidation with volume building ({vol_ratio:.2f}×)")
        return _candidate(
            SignalFamily.BREAKOUT_CONTINUATION, horizon, snapshot, bundle, rationale, watch=True
        )
    return None


def rule_fundamental_inflection(
    snapshot: InstrumentSnapshot, bundle: ScoreBundle, settings: Settings
) -> SignalCandidate | None:
    mom = _details(bundle, "momentum")
    v = _score(bundle, "valuation")
    breadth = mom.get("revision_breadth_30d") or 0.0
    conds = {
        "margin inflection detected": bool(mom.get("margin_inflection")),
        f"revision breadth {breadth:+.2f} ≥ +0.15": breadth >= 0.15,
        f"valuation {v if v is not None else 0:.1f} ≥ 4.0": (v or 0) >= 4.0,
        "medium gate passed": _gate_ok(bundle, "medium"),
    }
    rationale = [("✓ " if ok else "✗ ") + c for c, ok in conds.items()]
    if all(conds.values()):
        return _candidate(
            SignalFamily.FUNDAMENTAL_INFLECTION, "medium", snapshot, bundle, rationale
        )
    return None


def rule_estimate_momentum(
    snapshot: InstrumentSnapshot, bundle: ScoreBundle, settings: Settings
) -> SignalCandidate | None:
    mom = _details(bundle, "momentum")
    m_score = _score(bundle, "momentum")
    breadth = mom.get("revision_breadth_30d") or 0.0
    mag = mom.get("revision_magnitude_90d") or 0.0
    if m_score is None:
        return None
    conds = {
        f"revision breadth {breadth:+.2f} ≥ +0.25": breadth >= 0.25,
        f"revision magnitude {mag * 100:+.1f}% ≥ +3%": mag >= 0.03,
        f"momentum {m_score:.1f} ≥ 6.0": m_score >= 6.0,
        "medium gate passed": _gate_ok(bundle, "medium"),
    }
    rationale = [("✓ " if ok else "✗ ") + c for c, ok in conds.items()]
    if all(conds.values()):
        return _candidate(SignalFamily.ESTIMATE_MOMENTUM, "medium", snapshot, bundle, rationale)
    return None


def rule_avoid(
    snapshot: InstrumentSnapshot, bundle: ScoreBundle, settings: Settings
) -> SignalCandidate | None:
    """Informational risk warning — not gated on opportunity."""
    trap = _details(bundle, "valuation").get("value_trap") or {}
    red_flags = _details(bundle, "quality").get("red_flags") or []
    med = bundle.horizons["medium"]
    val_abs = None
    v = bundle.engine_results.get("valuation")
    if v is not None:
        val_abs = v.components.get("absolute")
    looks_cheap_trap = bool(trap.get("is_trap_risk")) and (val_abs or 0) >= 6.0
    conds = {
        f"risk {med.risk:.1f} ≥ 8.0": med.risk >= 8.0,
        "cheap-looking value trap": looks_cheap_trap,
        f"accounting red flags ≥ 3 ({len(red_flags)})": len(red_flags) >= 3,
    }
    if not any(conds.values()) or med.confidence < 4.0:
        return None
    rationale = [("✓ " if ok else "· ") + c for c, ok in conds.items() if ok]
    rationale += [f"trap checks failed: {', '.join(trap.get('failed_checks', []))}"] if looks_cheap_trap else []
    return _candidate(SignalFamily.AVOID, "medium", snapshot, bundle, rationale)


BUY_RULES = (
    rule_deep_value,
    rule_quality_compounder,
    rule_oversold_at_support,
    rule_constructive_pullback,
    rule_breakout,
    rule_fundamental_inflection,
    rule_estimate_momentum,
)


def generate_candidates(
    snapshot: InstrumentSnapshot, bundle: ScoreBundle, settings: Settings
) -> list[SignalCandidate]:
    """Fresh candidates for one instrument on one scan date.

    Selectivity rules (brief principle #9 — useful, selective alerts):
    * An AVOID verdict suppresses all buy-side candidates from the scan.
    * At most ONE buy candidate per horizon: several families firing at
      once on the same name and horizon is one thesis wearing several
      hats, so the strongest (triggered beats watch, then higher
      opportunity) survives and the suppressed families are recorded in
      its rationale.
    """
    raw: list[SignalCandidate] = []
    for rule in BUY_RULES:
        cand = rule(snapshot, bundle, settings)
        if cand is not None:
            raw.append(cand)
    avoid = rule_avoid(snapshot, bundle, settings)
    if avoid is not None:
        return [avoid]

    best_by_horizon: dict[str, SignalCandidate] = {}
    for cand in raw:
        cur = best_by_horizon.get(cand.horizon)
        if cur is None:
            best_by_horizon[cand.horizon] = cand
            continue
        stronger = (
            (cand.state_hint == "TRIGGERED", cand.scores.opportunity)
            > (cur.state_hint == "TRIGGERED", cur.scores.opportunity)
        )
        winner, loser = (cand, cur) if stronger else (cur, cand)
        winner.rationale.append(
            f"· also qualified as {loser.family.value.replace('_', ' ')} "
            f"(suppressed: one signal per horizon per scan)"
        )
        best_by_horizon[cand.horizon] = winner
    return list(best_by_horizon.values())


# ---------------------------------------------------------------------------
# Portfolio-position rules (HOLD / TRIM / FULL_EXIT candidates)
# ---------------------------------------------------------------------------


def portfolio_rules(
    snapshot: InstrumentSnapshot,
    bundle: ScoreBundle,
    settings: Settings,
    position_weight_pct: float | None,
    sector_weight_pct: float | None,
) -> SignalCandidate | None:
    """For OWNED instruments only. Scores themselves are ownership-blind;
    this merely maps the same scores onto hold/trim/exit guidance and adds
    exposure checks."""
    med = bundle.horizons["medium"]
    tech = _details(bundle, "technical")
    trap = _details(bundle, "valuation").get("value_trap") or {}
    red_flags = _details(bundle, "quality").get("red_flags") or []

    exit_conds = {
        f"opportunity collapsed ({med.opportunity:.1f} < 3.5 with confidence ≥ 5)":
            med.opportunity < 3.5 and med.confidence >= 5.0,
        "value-trap risk confirmed": bool(trap.get("is_trap_risk")) and len(trap.get("failed_checks", [])) >= 3,
        f"accounting red flags ≥ 3 ({len(red_flags)})": len(red_flags) >= 3,
        f"risk extreme ({med.risk:.1f} ≥ 8.5)": med.risk >= 8.5,
    }
    if any(exit_conds.values()):
        rationale = [("✓ " if ok else "· ") + c for c, ok in exit_conds.items() if ok]
        return _candidate(SignalFamily.FULL_EXIT, "medium", snapshot, bundle, rationale)

    trim_conds = {
        "technically extreme (extension flag)": bool(tech.get("extended")),
        f"opportunity faded ({med.opportunity:.1f} < 4.5) while risk {med.risk:.1f} ≥ 6.5":
            med.opportunity < 4.5 and med.risk >= 6.5,
        f"position weight {position_weight_pct or 0:.1f}% > max "
        f"{settings.risk_policy.max_position_exposure_pct:.0f}%":
            (position_weight_pct or 0) > settings.risk_policy.max_position_exposure_pct,
        f"sector weight {sector_weight_pct or 0:.1f}% > max "
        f"{settings.risk_policy.max_sector_exposure_pct:.0f}%":
            (sector_weight_pct or 0) > settings.risk_policy.max_sector_exposure_pct,
    }
    if any(trim_conds.values()):
        rationale = [("✓ " if ok else "· ") + c for c, ok in trim_conds.items() if ok]
        return _candidate(SignalFamily.TRIM, "medium", snapshot, bundle, rationale)

    if med.opportunity >= 4.5 and med.risk < 7.5:
        return _candidate(
            SignalFamily.HOLD, "medium", snapshot, bundle,
            [f"opportunity {med.opportunity:.1f} adequate, risk {med.risk:.1f} contained"],
        )
    return None


def result_of(bundle: ScoreBundle, engine: str) -> EngineResult | None:
    return bundle.engine_results.get(engine)


def horizon_score(bundle: ScoreBundle, horizon: str) -> HorizonScore:
    return bundle.horizons[horizon]
