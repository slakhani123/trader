"""Composite scoring: blending, confidence penalties, risk adders, gates,
best-fit selection, explanation transparency."""

from __future__ import annotations

import pytest

from tests.factories import catalyst, make_snapshot, quarterly_fundamentals
from vigil.config import Settings
from vigil.schemas.core import EngineResult
from vigil.scoring.composite import best_fit, score_instrument
from vigil.scoring.gates import universe_eligible
from vigil.scoring.weights import WEIGHTS, config_hash, get_weights


def engine(name: str, score: float | None, components=None, details=None, dq=1.0,
           warnings=None) -> EngineResult:
    return EngineResult(
        engine=name, score=score, components=components or {},
        details=details or {}, data_quality=dq, warnings=warnings or [],
    )


def full_results(base: float = 7.0) -> dict[str, EngineResult]:
    return {
        "quality": engine("quality", base, {"quality": base, "growth": base, "balance_sheet": base}),
        "valuation": engine("valuation", base),
        "technical": engine("technical", base, details={"reward_risk": 3.0}),
        "momentum": engine("momentum", base),
        "sentiment": engine("sentiment", base),
        "catalyst": engine("catalyst", base),
        "regime": engine(
            "regime", 7.0,
            details={"regime_label": "bull", "regime_adjustment": 0.1,
                     "risk_score": 4.0, "liquidity_band": "high"},
        ),
    }


@pytest.fixture()
def settings() -> Settings:
    return Settings(debug=True)


class TestOpportunity:
    def test_uniform_components_score_near_input(self, settings):
        snap = make_snapshot(fundamentals=quarterly_fundamentals())
        bundle = score_instrument(snap, full_results(7.0), settings)
        for h in ("short", "medium", "long"):
            assert 6.8 <= bundle.horizons[h].opportunity <= 7.4

    def test_weights_differ_by_horizon(self, settings):
        """Technical strength should move the short horizon more than long."""
        snap = make_snapshot(fundamentals=quarterly_fundamentals())
        results = full_results(5.0)
        results["technical"] = engine("technical", 9.0, details={"reward_risk": 3.0})
        bundle = score_instrument(snap, results, settings)
        assert bundle.horizons["short"].opportunity > bundle.horizons["long"].opportunity + 0.5

    def test_missing_engine_renormalises_and_penalises_confidence(self, settings):
        snap = make_snapshot(fundamentals=quarterly_fundamentals())
        results = full_results(7.0)
        results["sentiment"] = engine("sentiment", None, dq=0.0)
        bundle = score_instrument(snap, results, settings)
        h = bundle.horizons["medium"]
        assert 6.0 <= h.opportunity <= 7.6  # renormalised, not dragged to zero
        full = score_instrument(snap, full_results(7.0), settings)
        assert h.confidence < full.horizons["medium"].confidence

    def test_explanation_lines_reconstruct_score(self, settings):
        snap = make_snapshot(fundamentals=quarterly_fundamentals())
        bundle = score_instrument(snap, full_results(7.0), settings)
        h = bundle.horizons["long"]
        contribs = [
            float(line.split("= ")[1])
            for line in h.explanation
            if line.startswith("[opportunity]") and "= " in line
        ]
        tilt = sum(
            float(line.split("tilt ")[1].split(" ")[0])
            for line in h.explanation
            if "regime tilt" in line
        )
        assert sum(contribs) + tilt == pytest.approx(h.opportunity, abs=0.06)


class TestConfidence:
    def test_disagreement_penalty(self, settings):
        snap = make_snapshot(fundamentals=quarterly_fundamentals())
        agree = score_instrument(snap, full_results(7.0), settings)
        mixed = full_results(7.0)
        mixed["valuation"] = engine("valuation", 1.0)
        mixed["momentum"] = engine("momentum", 9.5)
        mixed["quality"] = engine("quality", 2.0, {"quality": 2.0, "growth": 9.0, "balance_sheet": 1.0})
        disagree = score_instrument(snap, mixed, settings)
        assert disagree.horizons["medium"].confidence < agree.horizons["medium"].confidence

    def test_binary_event_hits_short_confidence_hardest(self, settings):
        snap = make_snapshot(
            fundamentals=quarterly_fundamentals(),
            catalysts=(catalyst(10, binary=True),),
        )
        results = full_results(7.0)
        results["catalyst"] = engine(
            "catalyst", 7.0, details={"binary_event_within_20d": True}
        )
        bundle = score_instrument(snap, results, settings)
        base = score_instrument(snap, full_results(7.0), settings)
        drop_short = base.horizons["short"].confidence - bundle.horizons["short"].confidence
        drop_long = base.horizons["long"].confidence - bundle.horizons["long"].confidence
        assert drop_short > drop_long

    def test_uncalibrated_penalty_always_present(self, settings):
        snap = make_snapshot(fundamentals=quarterly_fundamentals())
        bundle = score_instrument(snap, full_results(7.0), settings)
        assert any(
            "not yet calibrated" in line for line in bundle.horizons["short"].explanation
        )


class TestRisk:
    def test_risk_adders(self, settings):
        snap = make_snapshot(fundamentals=quarterly_fundamentals())
        results = full_results(7.0)
        results["quality"] = engine(
            "quality", 7.0, {"quality": 7, "growth": 7, "balance_sheet": 7},
            details={"refinancing_risk": True, "red_flags": ["a", "b"]},
        )
        results["valuation"] = engine(
            "valuation", 7.0, details={"value_trap": {"is_trap_risk": True, "failed_checks": ["x", "y"]}}
        )
        risky = score_instrument(snap, results, settings)
        base = score_instrument(snap, full_results(7.0), settings)
        assert risky.horizons["medium"].risk > base.horizons["medium"].risk + 1.5


class TestGatesAndBestFit:
    def test_gate_fails_on_low_confidence(self, settings):
        from vigil.schemas.core import DataQualityFlags

        snap = make_snapshot(
            fundamentals=quarterly_fundamentals(),
            quality=DataQualityFlags(completeness=0.3, missing=["fundamentals", "news"]),
        )
        results = full_results(8.0)
        for r in results.values():
            r.data_quality = 0.2  # stale/incomplete inputs
        bundle = score_instrument(snap, results, settings)
        gate = bundle.horizons["medium"].gate
        assert gate is not None and not gate.passed
        assert any("confidence" in f or "data quality" in f for f in gate.failures)

    def test_best_fit_requires_clear_margin(self, settings):
        snap = make_snapshot(fundamentals=quarterly_fundamentals())
        bundle = score_instrument(snap, full_results(7.5), settings)
        # Uniform inputs => no clear winner => no best fit.
        assert best_fit(bundle.horizons) is None

    def test_universe_gate_blocks_illiquid(self, settings):
        from vigil.schemas.core import LiquidityStats

        snap = make_snapshot(
            fundamentals=quarterly_fundamentals(),
            liquidity=LiquidityStats(
                market_cap_base=50e6,  # below 250m floor
                median_daily_traded_value_base=100_000.0,  # below 1m floor
                price_staleness_days=0,
            ),
        )
        ok, reasons = universe_eligible(snap, settings)
        assert not ok
        assert any("market cap" in r for r in reasons)
        assert any("traded value" in r for r in reasons)


class TestWeights:
    def test_all_versions_sum_to_one(self):
        for version in WEIGHTS:
            get_weights(version)  # raises if any horizon != 1.0

    def test_config_hash_stable(self):
        assert config_hash("v1.0.0") == config_hash("v1.0.0")

    def test_unknown_version_rejected(self):
        with pytest.raises(KeyError):
            get_weights("v999")
