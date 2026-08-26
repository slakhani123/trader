"""Tests for the regime classification + instrument risk engine."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from tests.factories import (
    AS_OF,
    catalyst,
    make_snapshot,
    price_frame,
    quarterly_fundamentals,
)
from vigil.config import Settings
from vigil.engines.regime import analyse
from vigil.schemas.core import (
    InstrumentSnapshot,
    LiquidityStats,
    ShortInterestRecord,
    SourceRef,
)

SETTINGS = Settings()

COMPONENT_KEYS = {"regime", "instrument_risk", "liquidity_risk"}
REGIME_LABELS = {"bull", "correction", "bear", "stress", "recovery", "choppy"}
DETAIL_KEYS = {
    "regime_label", "regime_adjustment", "risk_score", "risk_factors",
    "beta", "downside_beta", "realised_vol_1y", "max_drawdown_2y",
    "gap_risk_freq", "liquidity_band", "binary_event_risk", "momentum_crash_risk",
}


def _src(kind: str = "short_interest") -> SourceRef:
    return SourceRef(provider="test", source_type=kind, reference=f"test://{kind}")  # type: ignore[arg-type]


def _short(days_ago: int, pct_float: float) -> ShortInterestRecord:
    return ShortInterestRecord(
        as_of=AS_OF - timedelta(days=days_ago),
        shares_short=1e7,
        pct_float=pct_float,
        days_to_cover=3.0,
        source=_src(),
    )


def _flat_macro_series(value: float, days: int = 400) -> pd.Series:
    idx = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=days)
    return pd.Series(np.full(days, value), index=idx)


def _rising_series(start: float, end: float, days: int = 400) -> pd.Series:
    idx = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=days)
    return pd.Series(np.linspace(start, end, days), index=idx)


def _bull_bench() -> pd.Series:
    return price_frame(days=700, shape=[(0.0, 1.0, 0.18)], daily_vol=0.006, seed=21)["adj_close"]


def _bear_bench() -> pd.Series:
    return price_frame(
        days=700, shape=[(0.0, 0.6, 0.12), (0.6, 1.0, -0.30)], daily_vol=0.008, seed=22
    )["adj_close"]


def _calm_macro() -> dict[str, pd.Series]:
    return {
        "vix": _flat_macro_series(14.0),
        "us_credit_spread_bps": _flat_macro_series(110.0),
        "us_policy_rate": _flat_macro_series(3.5),
    }


def _stressed_macro() -> dict[str, pd.Series]:
    return {
        "vix": _rising_series(20.0, 40.0),
        "us_credit_spread_bps": _rising_series(150.0, 480.0),
        "us_policy_rate": _rising_series(3.0, 4.5),
    }


def _benign_snapshot() -> InstrumentSnapshot:
    return make_snapshot(
        prices=price_frame(days=700, shape=[(0.0, 1.0, 0.12)], daily_vol=0.012, seed=31),
        benchmark=_bull_bench(),
        fundamentals=quarterly_fundamentals(),
        macro=_calm_macro(),
    )


def _hostile_snapshot() -> InstrumentSnapshot:
    return make_snapshot(
        prices=price_frame(
            days=700, shape=[(0.0, 0.5, 0.10), (0.5, 1.0, -0.50)], daily_vol=0.03, seed=32
        ),
        benchmark=_bear_bench(),
        fundamentals=quarterly_fundamentals(debt=9000e6, cash=100e6, op_margin=0.11),
        macro=_stressed_macro(),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_benign_environment_scores_high_and_hostile_scores_low() -> None:
    benign = analyse(_benign_snapshot(), SETTINGS)
    hostile = analyse(_hostile_snapshot(), SETTINGS)

    assert benign.score is not None and hostile.score is not None
    assert benign.score > 6.5
    assert hostile.score < 4.0
    assert benign.score > hostile.score + 2.0

    assert benign.details["regime_label"] == "bull"
    assert benign.details["regime_adjustment"] == pytest.approx(0.25)
    assert hostile.details["regime_label"] in ("bear", "stress")
    assert hostile.details["regime_adjustment"] <= -0.5

    for result in (benign, hostile):
        assert result.evidence, "expected evidence to be emitted"
        assert len(result.evidence) >= 4
        for item in result.evidence:
            assert item.source.provider, f"evidence {item.key} lacks a provider"
            assert item.statement
    assert any(e.direction == "supports" for e in benign.evidence)
    assert any(e.direction == "contradicts" for e in hostile.evidence)


def test_risk_score_orders_hostile_above_benign() -> None:
    benign = analyse(_benign_snapshot(), SETTINGS)
    hostile = analyse(_hostile_snapshot(), SETTINGS)
    assert 0.0 <= benign.details["risk_score"] <= 10.0
    assert 0.0 <= hostile.details["risk_score"] <= 10.0
    assert hostile.details["risk_score"] > benign.details["risk_score"] + 2.0
    assert hostile.details["risk_factors"], "hostile snapshot should name its risk factors"


def test_score_and_components_within_bounds() -> None:
    for snap in (_benign_snapshot(), _hostile_snapshot()):
        result = analyse(snap, SETTINGS)
        assert result.score is not None and 0.0 <= result.score <= 10.0
        assert set(result.components) == COMPONENT_KEYS
        for name, value in result.components.items():
            assert 0.0 <= value <= 10.0, f"component {name} out of bounds: {value}"
        assert 0.0 <= result.data_quality <= 1.0
        assert -0.75 <= result.details["regime_adjustment"] <= 0.25


# ---------------------------------------------------------------------------
# Abstention & determinism
# ---------------------------------------------------------------------------


def test_abstains_when_benchmark_series_is_empty() -> None:
    snap = make_snapshot(benchmark=pd.Series(dtype=float))
    result = analyse(snap, SETTINGS)
    assert result.score is None
    assert result.warnings, "abstention must carry a reason"
    assert result.engine == "regime"


def test_determinism_same_snapshot_identical_result() -> None:
    snap = _hostile_snapshot()
    first = analyse(snap, SETTINGS)
    second = analyse(snap, SETTINGS)
    assert first.model_dump() == second.model_dump()


# ---------------------------------------------------------------------------
# Regime classification rules
# ---------------------------------------------------------------------------


def test_bear_without_macro_and_stress_with_extreme_macro() -> None:
    bear_only = analyse(
        make_snapshot(prices=price_frame(days=700, seed=33), benchmark=_bear_bench()),
        SETTINGS,
    )
    assert bear_only.details["regime_label"] == "bear"
    assert bear_only.details["regime_adjustment"] == pytest.approx(-0.50)

    stressed = analyse(
        make_snapshot(
            prices=price_frame(days=700, seed=33),
            benchmark=_bear_bench(),
            macro=_stressed_macro(),
        ),
        SETTINGS,
    )
    assert stressed.details["regime_label"] == "stress"
    assert stressed.details["regime_adjustment"] == pytest.approx(-0.75)
    assert stressed.components["regime"] < bear_only.components["regime"]


def test_recovery_label_after_crash_and_strong_rally() -> None:
    bench = price_frame(
        days=700,
        shape=[(0.0, 0.6, 0.15), (0.6, 0.92, -0.75), (0.92, 1.0, 0.75)],
        daily_vol=0.007,
        seed=24,
    )["adj_close"]
    result = analyse(
        make_snapshot(prices=price_frame(days=700, seed=34), benchmark=bench), SETTINGS
    )
    assert result.details["regime_label"] == "recovery"
    assert result.details["regime_adjustment"] == pytest.approx(0.10)


def test_correction_label_on_modest_pullback_in_uptrend() -> None:
    bench = price_frame(
        days=700,
        shape=[(0.0, 0.93, 0.20), (0.93, 1.0, -0.70)],
        daily_vol=0.006,
        seed=25,
    )["adj_close"]
    result = analyse(
        make_snapshot(prices=price_frame(days=700, seed=35), benchmark=bench), SETTINGS
    )
    assert result.details["regime_label"] == "correction"
    assert result.details["regime_adjustment"] == pytest.approx(-0.25)


def test_short_benchmark_history_falls_back_to_choppy_without_tilt() -> None:
    bench = price_frame(days=150, daily_vol=0.008, seed=26)["adj_close"]
    result = analyse(make_snapshot(prices=price_frame(days=700, seed=36), benchmark=bench),
                     SETTINGS)
    assert result.score is not None  # instrument risk still measurable
    assert result.details["regime_label"] == "choppy"
    assert result.details["regime_adjustment"] == 0.0
    assert any("too short" in w for w in result.warnings)
    assert result.components["regime"] == pytest.approx(5.0)  # dropped, shown neutral


# ---------------------------------------------------------------------------
# Instrument risk flags
# ---------------------------------------------------------------------------


def test_momentum_crash_vulnerability_flagged() -> None:
    prices = price_frame(days=700, shape=[(0.0, 0.6, 0.05), (0.6, 1.0, 0.90)], seed=37)
    crowded = analyse(
        make_snapshot(
            prices=prices, benchmark=_bear_bench(), short_interest=(_short(10, 14.0),)
        ),
        SETTINGS,
    )
    assert crowded.details["momentum_crash_risk"] is True
    flag = [e for e in crowded.evidence if e.key == "momentum_crash_risk"]
    assert flag and flag[0].direction == "contradicts"

    calm = analyse(
        make_snapshot(
            prices=prices, benchmark=_bear_bench(), short_interest=(_short(10, 2.0),)
        ),
        SETTINGS,
    )
    assert calm.details["momentum_crash_risk"] is False
    assert crowded.score is not None and calm.score is not None
    assert crowded.score < calm.score


def test_binary_event_proximity_flagged_within_30_days_only() -> None:
    near = analyse(
        make_snapshot(
            prices=price_frame(days=700, seed=38),
            benchmark=_bull_bench(),
            catalysts=(catalyst(days_ahead=10, kind="regulatory", binary=True),),
        ),
        SETTINGS,
    )
    far = analyse(
        make_snapshot(
            prices=price_frame(days=700, seed=38),
            benchmark=_bull_bench(),
            catalysts=(catalyst(days_ahead=60, kind="regulatory", binary=True),),
        ),
        SETTINGS,
    )
    assert near.details["binary_event_risk"] is True
    assert far.details["binary_event_risk"] is False
    assert near.score is not None and far.score is not None
    assert near.score < far.score
    assert any("binary" in f for f in near.details["risk_factors"])


def test_leverage_bands_are_sector_aware() -> None:
    prices = price_frame(days=700, seed=39)
    levered = analyse(
        make_snapshot(
            prices=prices,
            benchmark=_bull_bench(),
            fundamentals=quarterly_fundamentals(debt=9000e6, cash=100e6, op_margin=0.11),
        ),
        SETTINGS,
    )
    assert any("leverage" in f for f in levered.details["risk_factors"])

    # Banks are never judged on net debt / operating profit; without CET1
    # data their leverage is simply not assessed.
    bank = analyse(
        make_snapshot(
            prices=prices,
            benchmark=_bull_bench(),
            sector="Financials",
            industry="Regional Banks",
            fundamentals=quarterly_fundamentals(debt=9000e6, cash=100e6, op_margin=0.11),
        ),
        SETTINGS,
    )
    assert not any("leverage" in f for f in bank.details["risk_factors"])
    assert bank.score is not None and levered.score is not None
    assert bank.score > levered.score


def test_reit_with_rising_rates_is_rate_sensitive() -> None:
    macro = {"us_policy_rate": _rising_series(3.0, 4.5)}
    reit = analyse(
        make_snapshot(
            prices=price_frame(days=700, seed=40),
            benchmark=_bull_bench(),
            sector="Real Estate",
            industry="Equity REIT",
            fundamentals=quarterly_fundamentals(debt=9000e6, cash=100e6),
            macro=macro,
        ),
        SETTINGS,
    )
    assert any("rate sensitivity" in f for f in reit.details["risk_factors"])

    unlevered = analyse(
        make_snapshot(
            prices=price_frame(days=700, seed=40),
            benchmark=_bull_bench(),
            fundamentals=quarterly_fundamentals(debt=100e6, cash=800e6),
            macro=macro,
        ),
        SETTINGS,
    )
    assert not any("rate sensitivity" in f for f in unlevered.details["risk_factors"])


# ---------------------------------------------------------------------------
# Liquidity
# ---------------------------------------------------------------------------


def _liq(traded: float | None, spread: float | None = 10.0) -> LiquidityStats:
    return LiquidityStats(
        market_cap_local=1e9,
        market_cap_base=0.78e9,
        median_daily_traded_value_local=traded,
        median_daily_traded_value_base=traded,
        spread_estimate_bps=spread,
        price_staleness_days=0,
    )


def test_liquidity_bands_and_spread_downgrade() -> None:
    high = analyse(
        make_snapshot(prices=price_frame(days=700, seed=41), benchmark=_bull_bench(),
                      liquidity=_liq(50e6)),
        SETTINGS,
    )
    assert high.details["liquidity_band"] == "high"

    thin = analyse(
        make_snapshot(prices=price_frame(days=700, seed=41), benchmark=_bull_bench(),
                      liquidity=_liq(0.5e6)),
        SETTINGS,
    )
    assert thin.details["liquidity_band"] == "very_low"
    assert any("liquidity" in f for f in thin.details["risk_factors"])
    assert high.score is not None and thin.score is not None
    assert high.score > thin.score

    wide_spread = analyse(
        make_snapshot(prices=price_frame(days=700, seed=41), benchmark=_bull_bench(),
                      liquidity=_liq(50e6, spread=90.0)),
        SETTINGS,
    )
    assert wide_spread.details["liquidity_band"] == "medium"  # downgraded one band


def test_missing_traded_value_is_conservative_not_fabricated() -> None:
    snap = make_snapshot(
        prices=price_frame(days=700, seed=42),
        benchmark=_bull_bench(),
        liquidity=LiquidityStats(),
    )
    result = analyse(snap, SETTINGS)
    assert result.score is not None
    assert result.details["liquidity_band"] == "low"
    assert result.components["liquidity_risk"] == pytest.approx(5.0)  # dropped from blend
    assert any("traded value unknown" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Details contract
# ---------------------------------------------------------------------------


def test_details_contract_keys_and_types() -> None:
    for snap in (_benign_snapshot(), _hostile_snapshot()):
        result = analyse(snap, SETTINGS)
        d = result.details
        assert set(d) >= DETAIL_KEYS

        assert d["regime_label"] in REGIME_LABELS
        assert isinstance(d["regime_adjustment"], float)
        assert -0.75 <= d["regime_adjustment"] <= 0.25
        assert isinstance(d["risk_score"], float) and 0.0 <= d["risk_score"] <= 10.0
        assert isinstance(d["risk_factors"], list)
        assert all(isinstance(f, str) for f in d["risk_factors"])
        for key in ("beta", "downside_beta", "realised_vol_1y", "max_drawdown_2y",
                    "gap_risk_freq"):
            assert d[key] is None or isinstance(d[key], float)
        assert d["liquidity_band"] in ("high", "medium", "low", "very_low")
        assert isinstance(d["binary_event_risk"], bool)
        assert isinstance(d["momentum_crash_risk"], bool)


def test_full_history_populates_risk_metrics() -> None:
    result = analyse(_benign_snapshot(), SETTINGS)
    d = result.details
    assert d["beta"] is not None
    assert d["downside_beta"] is not None
    assert d["realised_vol_1y"] is not None and d["realised_vol_1y"] > 0
    assert d["max_drawdown_2y"] is not None and d["max_drawdown_2y"] <= 0
    assert d["gap_risk_freq"] is not None and 0.0 <= d["gap_risk_freq"] <= 1.0
