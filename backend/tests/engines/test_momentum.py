"""Tests for the cross-sectional + fundamental momentum engine."""

from __future__ import annotations

from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from tests.factories import (
    AS_OF,
    catalyst,
    estimate,
    make_snapshot,
    news_item,
    price_frame,
    quarterly_fundamentals,
)
from vigil.config import Settings
from vigil.engines.momentum import analyse
from vigil.schemas.core import CatalystRecord, InstrumentSnapshot, ShortInterestRecord, SourceRef

SETTINGS = Settings()

COMPONENT_KEYS = {"price_momentum", "fundamental_momentum", "confirmation"}
RETURN_KEYS = {"m1", "m3", "m6", "m12", "m12_1"}
RS_KEYS = {"market_1m", "market_3m", "market_6m", "sector_3m"}


def _src(kind: str = "news") -> SourceRef:
    return SourceRef(provider="test", source_type=kind, reference=f"test://{kind}")  # type: ignore[arg-type]


def _resolved_earnings(days_ago: int, outcome: str) -> CatalystRecord:
    when = AS_OF - timedelta(days=days_ago)
    return CatalystRecord(
        record_id=f"earn-{days_ago}",
        kind="earnings",
        expected_date=when,
        date_confirmed=True,
        description="Quarterly results",
        binary=False,
        published_at=datetime.combine(when - timedelta(days=30), time(9)),
        resolved=True,
        outcome=outcome,
        outcome_date=when,
        source=_src("news"),
    )


def _short(days_ago: int, pct_float: float) -> ShortInterestRecord:
    return ShortInterestRecord(
        as_of=AS_OF - timedelta(days=days_ago),
        shares_short=1e7,
        pct_float=pct_float,
        days_to_cover=3.0,
        source=_src("short_interest"),
    )


def _strong_snapshot(catalysts: tuple[CatalystRecord, ...] | None = None) -> InstrumentSnapshot:
    prices = price_frame(days=700, shape=[(0.0, 0.6, 0.10), (0.6, 1.0, 0.40)], seed=11)
    return make_snapshot(
        prices=prices,
        fundamentals=quarterly_fundamentals(quarters=12, revenue_growth_q=0.04),
        estimates=(estimate(mean=5.0, mean_30d_ago=4.8, mean_90d_ago=4.5, up=7, down=1),),
        catalysts=catalysts
        if catalysts is not None
        else (_resolved_earnings(40, "EPS surprise +6.0%"),),
        news=(news_item(26, 0.7, headline="TestCo: FY guidance raised on strong demand"),),
    )


def _weak_snapshot() -> InstrumentSnapshot:
    prices = price_frame(days=700, shape=[(0.0, 0.5, 0.02), (0.5, 1.0, -0.35)], seed=12)
    return make_snapshot(
        prices=prices,
        fundamentals=quarterly_fundamentals(quarters=12, revenue_growth_q=-0.02),
        estimates=(estimate(mean=4.0, mean_30d_ago=4.4, mean_90d_ago=4.8, up=0, down=7),),
        catalysts=(_resolved_earnings(40, "EPS surprise -7.0%"),),
        news=(news_item(26, -0.75, headline="TestCo: FY guidance cut on weak demand"),),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_strong_momentum_scores_high_and_weak_scores_low() -> None:
    strong = analyse(_strong_snapshot(), SETTINGS)
    weak = analyse(_weak_snapshot(), SETTINGS)

    assert strong.score is not None and weak.score is not None
    assert strong.score > 6.5
    assert weak.score < 3.0
    assert strong.score > weak.score + 3.0

    for result in (strong, weak):
        assert result.evidence, "expected evidence to be emitted"
        for item in result.evidence:
            assert item.source.provider, f"evidence {item.key} lacks a provider"
            assert item.statement
    assert len(strong.evidence) >= 4
    assert any(e.direction == "supports" for e in strong.evidence)
    assert any(e.direction == "contradicts" for e in weak.evidence)


def test_score_and_components_within_bounds() -> None:
    for snap in (_strong_snapshot(), _weak_snapshot()):
        result = analyse(snap, SETTINGS)
        assert result.score is not None and 0.0 <= result.score <= 10.0
        assert set(result.components) == COMPONENT_KEYS
        for name, value in result.components.items():
            assert 0.0 <= value <= 10.0, f"component {name} out of bounds: {value}"
        assert 0.0 <= result.data_quality <= 1.0


def test_confluence_bonus_flagged_when_price_revisions_and_rs_agree() -> None:
    result = analyse(_strong_snapshot(), SETTINGS)
    confluence = [e for e in result.evidence if e.key == "momentum_confluence"]
    assert confluence and confluence[0].direction == "supports"


# ---------------------------------------------------------------------------
# Abstention & determinism
# ---------------------------------------------------------------------------


def test_abstains_with_fewer_than_260_bars() -> None:
    snap = make_snapshot(prices=price_frame(days=200))
    result = analyse(snap, SETTINGS)
    assert result.score is None
    assert result.warnings, "abstention must carry a reason"
    assert result.engine == "momentum"


def test_determinism_same_snapshot_identical_result() -> None:
    snap = _strong_snapshot()
    first = analyse(snap, SETTINGS)
    second = analyse(snap, SETTINGS)
    assert first.model_dump() == second.model_dump()


# ---------------------------------------------------------------------------
# Penalties
# ---------------------------------------------------------------------------


def test_parabolic_extension_penalised() -> None:
    prices = price_frame(days=700, shape=[(0.0, 0.85, 0.05), (0.85, 1.0, 3.8)], seed=13)
    snap = make_snapshot(prices=prices)
    result = analyse(snap, SETTINGS)
    assert result.details["parabolic"] is True
    assert "parabolic" in result.details["penalties"]
    penalty = [e for e in result.evidence if e.key == "penalty_parabolic"]
    assert penalty and penalty[0].direction == "contradicts"


def test_binary_event_within_10_days_reduces_score() -> None:
    resolved = _resolved_earnings(40, "EPS surprise +6.0%")
    without = analyse(_strong_snapshot(catalysts=(resolved,)), SETTINGS)
    with_binary = analyse(
        _strong_snapshot(catalysts=(resolved, catalyst(days_ahead=7, binary=True))),
        SETTINGS,
    )
    assert without.score is not None and with_binary.score is not None
    assert "binary_event_within_10d" in with_binary.details["penalties"]
    assert "binary_event_within_10d" not in without.details["penalties"]
    assert with_binary.score == pytest.approx(without.score - 0.75, abs=0.02)


def test_negative_divergence_price_up_revisions_down() -> None:
    prices = price_frame(days=700, shape=[(0.0, 0.6, 0.05), (0.6, 1.0, 0.45)], seed=14)
    snap = make_snapshot(
        prices=prices,
        estimates=(estimate(mean=4.2, mean_30d_ago=4.5, mean_90d_ago=4.8, up=0, down=6),),
    )
    result = analyse(snap, SETTINGS)
    assert "negative_divergence" in result.details["penalties"]
    penalty = [e for e in result.evidence if e.key == "penalty_negative_divergence"]
    assert penalty and penalty[0].direction == "contradicts"


def test_crowding_penalty_on_rising_short_interest() -> None:
    prices = price_frame(days=700, shape=[(0.0, 0.6, 0.05), (0.6, 1.0, 0.40)], seed=15)
    crowded = make_snapshot(
        prices=prices, short_interest=(_short(30, 12.0), _short(10, 14.5))
    )
    calm = make_snapshot(prices=prices, short_interest=(_short(30, 3.0), _short(10, 2.5)))
    crowded_result = analyse(crowded, SETTINGS)
    calm_result = analyse(calm, SETTINGS)
    assert "crowding" in crowded_result.details["penalties"]
    assert "crowding" not in calm_result.details["penalties"]
    assert crowded_result.score is not None and calm_result.score is not None
    assert crowded_result.score < calm_result.score


# ---------------------------------------------------------------------------
# Fundamental momentum edges
# ---------------------------------------------------------------------------


def test_margin_inflection_detected_and_supported() -> None:
    overrides = {
        i: {"operating_income": 1000e6 * (1.02 ** (i + 1)) * 0.10} for i in range(8, 12)
    }
    funds = quarterly_fundamentals(quarters=12, op_margin=0.05, overrides=overrides)
    snap = make_snapshot(prices=price_frame(days=700, seed=16), fundamentals=funds)
    result = analyse(snap, SETTINGS)
    assert result.details["margin_inflection"] is True
    inflection = [e for e in result.evidence if e.key == "margin_inflection"]
    assert inflection and inflection[0].direction == "supports"

    flat = analyse(
        make_snapshot(
            prices=price_frame(days=700, seed=16),
            fundamentals=quarterly_fundamentals(quarters=12, op_margin=0.05),
        ),
        SETTINGS,
    )
    assert flat.details["margin_inflection"] is False


def test_earnings_surprise_parsing_and_unparseable_outcomes() -> None:
    parsed = analyse(
        make_snapshot(
            prices=price_frame(days=700, seed=17),
            catalysts=(
                _resolved_earnings(130, "EPS surprise -1.0%"),
                _resolved_earnings(40, "EPS surprise +5.2%"),
            ),
        ),
        SETTINGS,
    )
    assert parsed.details["surprise_last"] == pytest.approx(5.2)

    unparseable = analyse(
        make_snapshot(
            prices=price_frame(days=700, seed=17),
            catalysts=(_resolved_earnings(40, "Beat expectations across the board"),),
        ),
        SETTINGS,
    )
    assert unparseable.details["surprise_last"] is None


def test_missing_estimates_renormalises_and_warns() -> None:
    snap = make_snapshot(prices=price_frame(days=700, seed=18))
    result = analyse(snap, SETTINGS)
    assert result.score is not None  # no abstention: price momentum still measurable
    assert result.details["revision_breadth_30d"] is None
    assert result.details["revision_magnitude_90d"] is None
    assert any("estimates" in w for w in result.warnings)
    assert result.data_quality < 0.9


# ---------------------------------------------------------------------------
# Accumulation breakout
# ---------------------------------------------------------------------------


def _breakout_frame() -> pd.DataFrame:
    idx = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=700)
    close = 100.0 + np.sin(np.arange(700)) * 0.5
    close[-10:] += 15.0  # decisive move above a long flat base
    opens = np.r_[close[0], close[:-1]]  # no overnight gaps
    volume = np.full(700, 1.0e6)
    volume[-10:] = 3.0e6
    return pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, close) * 1.001,
            "low": np.minimum(opens, close) * 0.999,
            "close": close,
            "adj_close": close,
            "adj_open": opens,
            "volume": volume,
        },
        index=idx,
    )


def test_accumulation_breakout_flagged_with_volume() -> None:
    snap = make_snapshot(prices=_breakout_frame())
    result = analyse(snap, SETTINGS)
    assert result.details["accumulation_breakout"] is True
    breakout = [e for e in result.evidence if e.key == "accumulation_breakout"]
    assert breakout and breakout[0].direction == "supports"


# ---------------------------------------------------------------------------
# Details contract
# ---------------------------------------------------------------------------


def test_details_contract_keys_and_types() -> None:
    for snap in (_strong_snapshot(), _weak_snapshot()):
        result = analyse(snap, SETTINGS)
        d = result.details

        assert set(d["returns"]) == RETURN_KEYS
        for v in d["returns"].values():
            assert v is None or isinstance(v, float)
        assert set(d["rs"]) == RS_KEYS
        for v in d["rs"].values():
            assert v is None or isinstance(v, float)
        assert d["revision_breadth_30d"] is None or isinstance(d["revision_breadth_30d"], float)
        assert d["revision_magnitude_90d"] is None or isinstance(
            d["revision_magnitude_90d"], float
        )
        assert d["surprise_last"] is None or isinstance(d["surprise_last"], float)
        assert isinstance(d["margin_inflection"], bool)
        assert isinstance(d["penalties"], list)
        assert all(isinstance(p, str) for p in d["penalties"])
        assert isinstance(d["parabolic"], bool)
        assert isinstance(d["accumulation_breakout"], bool)


def test_returns_and_rs_populated_with_full_history() -> None:
    result = analyse(_strong_snapshot(), SETTINGS)
    d = result.details
    assert all(v is not None for v in d["returns"].values())
    assert d["returns"]["m6"] > 0
    assert d["rs"]["market_3m"] is not None
    assert d["rs"]["sector_3m"] is None  # no sector index supplied
    assert d["revision_breadth_30d"] == pytest.approx(0.5)
