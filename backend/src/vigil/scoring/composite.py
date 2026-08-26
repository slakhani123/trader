"""Composite multi-horizon scoring.

Blends the seven engine results into per-horizon Opportunity / Confidence /
Risk using the versioned weight tables, with fully transparent contribution
lines. Opportunity strength and evidence confidence are deliberately
separate numbers (brief principle #6).

Confidence is COMPUTED from evidence properties, never averaged from
opportunity inputs. It falls when: data is stale/incomplete, engines
disagree, analyst coverage is sparse, a binary event dominates, the name is
illiquid, the model is uncalibrated, or one indicator dominates the blend.
"""

from __future__ import annotations

import math
from datetime import date

from vigil.config import HORIZONS, Settings
from vigil.indicators.stats import clamp
from vigil.schemas.core import (
    EngineResult,
    Evidence,
    HorizonScore,
    InstrumentSnapshot,
    ScoreBundle,
)
from vigil.scoring import gates as gates_mod
from vigil.scoring.weights import get_weights

# The model has no out-of-sample calibration history yet; every confidence
# carries this documented penalty until backtest calibration lifts it.
UNCALIBRATED_PENALTY = 0.5


def extract_components(results: dict[str, EngineResult]) -> dict[str, float | None]:
    """Map engine outputs onto the nine displayed component scores."""

    def sub(engine: str, key: str) -> float | None:
        r = results.get(engine)
        if r is None or r.score is None:
            return None
        return r.components.get(key, r.score)

    def top(engine: str) -> float | None:
        r = results.get(engine)
        return None if r is None else r.score

    return {
        "quality": sub("quality", "quality"),
        "growth": sub("quality", "growth"),
        "balance_sheet": sub("quality", "balance_sheet"),
        "valuation": top("valuation"),
        "technical": top("technical"),
        "momentum": top("momentum"),
        "sentiment": top("sentiment"),
        "catalysts": top("catalyst"),
    }


def overall_data_quality(
    snapshot: InstrumentSnapshot, results: dict[str, EngineResult]
) -> float:
    engine_dq = [r.data_quality for r in results.values()]
    mean_engine = sum(engine_dq) / len(engine_dq) if engine_dq else 0.0
    return 0.5 * snapshot.quality.completeness + 0.5 * mean_engine


def opportunity_for(
    horizon: str,
    components: dict[str, float | None],
    results: dict[str, EngineResult],
    weights: dict[str, float],
) -> tuple[float, list[str], float]:
    """Weighted blend over available components with renormalisation.
    Returns (score, explanation_lines, max_weight_share)."""
    available = {k: v for k, v in components.items() if v is not None}
    lines: list[str] = []
    if not available:
        return 0.0, ["no engine produced a score"], 1.0
    total_w = sum(weights[k] for k in available)
    score = 0.0
    max_share = 0.0
    for key in sorted(available, key=lambda k: -weights[k]):
        w = weights[key] / total_w
        contrib = available[key] * w
        score += contrib
        max_share = max(max_share, w)
        lines.append(
            f"{key} {available[key]:.1f} × weight {w:.2f} = {contrib:+.2f}"
        )
    missing = [k for k, v in components.items() if v is None]
    if missing:
        lines.append(f"unavailable (weights renormalised): {', '.join(missing)}")
    # Regime tilt: modest, documented, short/medium only.
    regime = results.get("regime")
    if regime is not None and regime.score is not None:
        adj = float(regime.details.get("regime_adjustment", 0.0))
        factor = {"short": 1.0, "medium": 0.5, "long": 0.0}[horizon]
        if adj != 0.0 and factor > 0:
            tilt = clamp(adj * factor, -0.75, 0.25)
            score += tilt
            lines.append(
                f"regime tilt {tilt:+.2f} ({regime.details.get('regime_label', 'unknown')})"
            )
    return clamp(score), lines, max_share


def confidence_for(
    horizon: str,
    snapshot: InstrumentSnapshot,
    results: dict[str, EngineResult],
    components: dict[str, float | None],
    dq: float,
    max_weight_share: float,
) -> tuple[float, list[str]]:
    lines: list[str] = []
    conf = 10.0 * (0.35 + 0.65 * dq)  # data completeness/freshness base
    lines.append(f"base from data quality {dq:.2f} → {conf:.1f}")

    reporting = gates_mod.engines_reporting(results)
    if reporting < len(results):
        penalty = 0.7 * (len(results) - reporting)
        conf -= penalty
        lines.append(f"−{penalty:.1f}: {len(results) - reporting} engine(s) abstained")

    # Disagreement between evidence dimensions.
    vals = [v for v in components.values() if v is not None]
    if len(vals) >= 3:
        mean = sum(vals) / len(vals)
        std = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
        if std > 1.8:
            penalty = min(2.0, (std - 1.8) * 1.2)
            conf -= penalty
            lines.append(f"−{penalty:.1f}: engines disagree (component std {std:.1f})")

    # Contradictory evidence share.
    all_ev: list[Evidence] = [e for r in results.values() for e in r.evidence]
    contra = sum(1 for e in all_ev if e.direction == "contradicts")
    if all_ev and contra / len(all_ev) > 0.35:
        conf -= 1.0
        lines.append(f"−1.0: heavy contradictory evidence ({contra}/{len(all_ev)})")

    # Sparse analyst coverage.
    tgt = snapshot.target
    if not snapshot.estimates:
        conf -= 1.0
        lines.append("−1.0: no analyst estimates available")
    elif tgt is not None and tgt.analyst_count < 5:
        conf -= 0.7
        lines.append(f"−0.7: sparse analyst coverage ({tgt.analyst_count})")

    # Binary event dominance (harshest for the short horizon).
    cat = results.get("catalyst")
    if cat is not None and cat.details.get("binary_event_within_20d"):
        penalty = {"short": 1.5, "medium": 0.7, "long": 0.3}[horizon]
        conf -= penalty
        lines.append(f"−{penalty:.1f}: binary event within 20 trading days")

    # Illiquidity.
    regime = results.get("regime")
    band = (regime.details.get("liquidity_band") if regime else None) or "high"
    if band == "low":
        conf -= 1.0
        lines.append("−1.0: low liquidity")
    elif band == "very_low":
        conf -= 2.0
        lines.append("−2.0: very low liquidity")

    # Single-indicator dependence.
    if max_weight_share > 0.45:
        conf -= 1.0
        lines.append(f"−1.0: blend leans on one component ({max_weight_share:.0%})")

    # Stale prices.
    if snapshot.liquidity.price_staleness_days > 3:
        conf -= 1.5
        lines.append(f"−1.5: prices stale ({snapshot.liquidity.price_staleness_days}d)")

    conf -= UNCALIBRATED_PENALTY
    lines.append(f"−{UNCALIBRATED_PENALTY:.1f}: model not yet calibrated out-of-sample")
    return clamp(conf), lines


def risk_for(
    horizon: str,
    snapshot: InstrumentSnapshot,
    results: dict[str, EngineResult],
) -> tuple[float, list[str]]:
    lines: list[str] = []
    regime = results.get("regime")
    base = float(regime.details.get("risk_score", 5.0)) if regime else 5.0
    risk = base
    lines.append(f"instrument/environment base risk {base:.1f}")

    cat = results.get("catalyst")
    if cat is not None and cat.details.get("binary_event_within_20d") and horizon != "long":
        risk += 1.0
        lines.append("+1.0: binary event within 20 trading days")

    q = results.get("quality")
    if q is not None:
        if q.details.get("refinancing_risk"):
            risk += 1.0
            lines.append("+1.0: refinancing risk")
        if len(q.details.get("red_flags", [])) >= 2:
            risk += 0.7
            lines.append("+0.7: multiple accounting red flags")

    v = results.get("valuation")
    if v is not None and v.details.get("value_trap", {}).get("is_trap_risk"):
        risk += 0.8
        lines.append("+0.8: value-trap characteristics")

    t = results.get("technical")
    if t is not None and t.details.get("extended") and horizon == "short":
        risk += 0.7
        lines.append("+0.7: technically extended")

    s = results.get("sentiment")
    share = (s.details.get("share_by_type", {}) if s else {}).get("social", 0.0)
    if share and share > 0.4:
        risk += 0.6
        lines.append("+0.6: social-speculation-driven flow")

    return clamp(risk), lines


def best_fit(horizons: dict[str, HorizonScore]) -> str | None:
    """Pick a best-fit horizon only when one clearly dominates: highest
    gated opportunity, ≥0.75 ahead of the runner-up, confidence ≥ 5."""
    eligible = {
        h: s for h, s in horizons.items()
        if not s.abstained and s.confidence >= 5.0 and (s.gate is None or s.gate.passed)
    }
    if not eligible:
        return None
    ranked = sorted(eligible.items(), key=lambda kv: -kv[1].opportunity)
    if len(ranked) == 1:
        return ranked[0][0]
    if ranked[0][1].opportunity - ranked[1][1].opportunity >= 0.75:
        return ranked[0][0]
    return None


def score_instrument(
    snapshot: InstrumentSnapshot,
    results: dict[str, EngineResult],
    settings: Settings,
    as_of: date | None = None,
) -> ScoreBundle:
    as_of = as_of or snapshot.as_of
    weights = get_weights(settings.scoring_model_version)
    components = extract_components(results)
    dq = overall_data_quality(snapshot, results)
    reporting = gates_mod.engines_reporting(results)

    technical = results.get("technical")
    reward_risk = technical.details.get("reward_risk") if technical else None

    horizons: dict[str, HorizonScore] = {}
    for h in HORIZONS:
        opp, opp_lines, max_share = opportunity_for(h, components, results, weights[h])
        conf, conf_lines = confidence_for(h, snapshot, results, components, dq, max_share)
        risk, risk_lines = risk_for(h, snapshot, results)

        abstain_reasons: list[str] = []
        if reporting < settings.gates.min_engines_reporting:
            abstain_reasons.append(
                f"only {reporting}/{len(results)} engines reported"
            )
        if dq < settings.gates.min_data_quality:
            abstain_reasons.append(f"data quality {dq:.2f} below floor")

        gate = gates_mod.buy_gate(
            opp, conf, risk, dq, reporting,
            reward_risk if isinstance(reward_risk, int | float) else None,
            snapshot, settings,
        )
        shown_components = {k: round(v, 2) for k, v in components.items() if v is not None}
        shown_components["data_quality"] = round(dq * 10, 2)
        horizons[h] = HorizonScore(
            horizon=h,  # type: ignore[arg-type]
            opportunity=round(opp, 2),
            confidence=round(conf, 2),
            risk=round(risk, 2),
            components=shown_components,
            abstained=bool(abstain_reasons),
            abstain_reasons=abstain_reasons,
            gate=gate,
            explanation=(
                [f"[opportunity] {ln}" for ln in opp_lines]
                + [f"[confidence] {ln}" for ln in conf_lines]
                + [f"[risk] {ln}" for ln in risk_lines]
            ),
        )

    evidence: list[Evidence] = []
    seen: set[tuple[str, str]] = set()
    for r in results.values():
        for e in r.evidence:
            key = (e.key, e.statement)
            if key not in seen:
                seen.add(key)
                evidence.append(e)
    warnings = sorted({w for r in results.values() for w in r.warnings})

    return ScoreBundle(
        instrument_id=snapshot.info.instrument_id,
        as_of=as_of,
        model_version=settings.scoring_model_version,
        horizons=horizons,
        best_fit_horizon=best_fit(horizons),
        engine_results=results,
        evidence=evidence,
        warnings=warnings,
    )
