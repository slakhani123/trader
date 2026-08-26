"""Tests for the forward catalyst-calendar engine."""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pandas as pd

from tests.factories import (
    AS_OF,
    catalyst,
    make_snapshot,
    news_item,
    price_frame,
    quarterly_fundamentals,
)
from vigil.config import Settings
from vigil.engines.catalyst import analyse
from vigil.schemas.core import CatalystRecord, DataQualityFlags, InstrumentSnapshot, SourceRef

SETTINGS = Settings()

COMPONENT_KEYS = {"near_term", "medium_term", "long_term", "recent_outcomes"}
UPCOMING_ITEM_KEYS = {
    "kind", "date", "days", "binary", "confirmed", "description",
    "priced_in_pct", "relevance",
}


def _src(kind: str = "news") -> SourceRef:
    return SourceRef(provider="test", source_type=kind, reference=f"test://{kind}")  # type: ignore[arg-type]


def _resolved(days_ago: int, kind: str, outcome: str) -> CatalystRecord:
    when = AS_OF - timedelta(days=days_ago)
    return CatalystRecord(
        record_id=f"res-{kind}-{days_ago}",
        kind=kind,  # type: ignore[arg-type]
        expected_date=when,
        date_confirmed=True,
        description=f"{kind} event",
        binary=False,
        published_at=datetime.combine(when - timedelta(days=20), time(9)),
        resolved=True,
        outcome=outcome,
        outcome_date=when,
        source=_src("news"),
    )


def _flat_market_snapshot(**kwargs) -> InstrumentSnapshot:
    """Snapshot whose benchmark tracks the instrument exactly, so the 20-day
    excess run-up is 0 and the priced-in heuristic never engages."""
    prices = kwargs.pop("prices", None)
    if prices is None:
        prices = price_frame(days=700, seed=7)
    return make_snapshot(prices=prices, benchmark=prices["adj_close"] * 0.5, **kwargs)


def _rich_snapshot() -> InstrumentSnapshot:
    return _flat_market_snapshot(
        catalysts=(
            catalyst(days_ahead=12, kind="earnings", confirmed=True),
            catalyst(days_ahead=20, kind="guidance", confirmed=True,
                     description="FY guidance update"),
            catalyst(days_ahead=60, kind="m_and_a", confirmed=True,
                     description="Bolt-on acquisition completion"),
            _resolved(25, "guidance", "Guidance raised on strong demand"),
        ),
        news=(news_item(10, 0.4),),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_rich_near_term_calendar_scores_high() -> None:
    result = analyse(_rich_snapshot(), SETTINGS)
    assert result.score is not None
    assert result.score > 6.0
    assert result.components["near_term"] > 5.5
    assert result.components["medium_term"] > 5.0
    assert result.components["recent_outcomes"] > 5.0
    assert len(result.evidence) >= 4
    assert any(e.direction == "supports" for e in result.evidence)
    for item in result.evidence:
        assert item.source.provider, f"evidence {item.key} lacks a provider"
        assert item.statement


def test_empty_calendar_with_news_scores_neutral_5() -> None:
    snap = _flat_market_snapshot(catalysts=(), news=(news_item(3, 0.1),))
    result = analyse(snap, SETTINGS)
    assert result.score == 5.0
    assert all(v == 5.0 for v in result.components.values())
    assert any("empty" in w or "no catalyst" in w for w in result.warnings)
    assert result.details["upcoming"] == []
    assert result.data_quality < 0.8


def test_score_and_components_within_bounds() -> None:
    result = analyse(_rich_snapshot(), SETTINGS)
    assert result.score is not None and 0.0 <= result.score <= 10.0
    assert set(result.components) == COMPONENT_KEYS
    for name, value in result.components.items():
        assert 0.0 <= value <= 10.0, f"component {name} out of bounds: {value}"
    assert 0.0 <= result.data_quality <= 1.0


# ---------------------------------------------------------------------------
# Abstention & determinism
# ---------------------------------------------------------------------------


def test_abstains_without_catalysts_and_without_news_coverage() -> None:
    snap = make_snapshot(
        catalysts=(),
        news=(),
        quality=DataQualityFlags(news_available=False, missing=["news"], completeness=0.4),
    )
    result = analyse(snap, SETTINGS)
    assert result.score is None
    assert result.warnings, "abstention must carry a reason"
    assert result.engine == "catalyst"


def test_no_catalysts_no_news_but_coverage_present_scores_neutral() -> None:
    # 'news' NOT in quality.missing: coverage exists, there simply are no events.
    snap = _flat_market_snapshot(catalysts=(), news=(), quality=DataQualityFlags())
    result = analyse(snap, SETTINGS)
    assert result.score == 5.0


def test_determinism_same_snapshot_identical_result() -> None:
    snap = _rich_snapshot()
    first = analyse(snap, SETTINGS)
    second = analyse(snap, SETTINGS)
    assert first.model_dump() == second.model_dump()


# ---------------------------------------------------------------------------
# Binary events
# ---------------------------------------------------------------------------


def test_near_term_binary_event_flagged_and_discounted() -> None:
    plain = _flat_market_snapshot(
        catalysts=(catalyst(days_ahead=10, kind="regulatory", binary=False),),
    )
    binary = _flat_market_snapshot(
        catalysts=(catalyst(days_ahead=10, kind="regulatory", binary=True),),
    )
    plain_res = analyse(plain, SETTINGS)
    binary_res = analyse(binary, SETTINGS)

    assert plain_res.details["binary_event_within_20d"] is False
    assert plain_res.details["next_binary"] is None
    assert binary_res.details["binary_event_within_20d"] is True
    assert binary_res.details["next_binary"] == {
        "kind": "regulatory",
        "date": (AS_OF + timedelta(days=10)).isoformat(),
        "days": 10,
    }
    assert binary_res.details["binary_event_within"] == {"days": 10, "kind": "regulatory"}

    proximity = [e for e in binary_res.evidence if e.key == "binary_event_proximity"]
    assert proximity and proximity[0].direction == "contradicts"

    assert plain_res.score is not None and binary_res.score is not None
    assert binary_res.score < plain_res.score
    assert binary_res.components["near_term"] < 5.0  # coin-flip is risk, not opportunity


def test_far_binary_event_contributes_less_than_non_binary() -> None:
    plain = analyse(
        _flat_market_snapshot(catalysts=(catalyst(days_ahead=60, kind="regulatory"),)),
        SETTINGS,
    )
    binary = analyse(
        _flat_market_snapshot(
            catalysts=(catalyst(days_ahead=60, kind="regulatory", binary=True),)
        ),
        SETTINGS,
    )
    assert plain.score is not None and binary.score is not None
    assert binary.details["binary_event_within_20d"] is False
    assert binary.details["next_binary"]["days"] == 60
    assert 5.0 < binary.components["medium_term"] < plain.components["medium_term"]


# ---------------------------------------------------------------------------
# Priced-in heuristic
# ---------------------------------------------------------------------------


def test_priced_in_runup_scales_upcoming_contribution() -> None:
    # Same noise (seed), drift differs only over the final ~21 bars: the
    # 20-day excess run-up vs the benchmark is ~+20%, inside the 10-25% band.
    spiky = price_frame(days=700, shape=[(0.0, 0.97, 0.02), (0.97, 1.0, 2.5)], seed=21)
    calm_bench = price_frame(
        days=700, shape=[(0.0, 0.97, 0.02), (0.97, 1.0, 0.02)], seed=21
    )["adj_close"]
    cats = (catalyst(days_ahead=15, kind="guidance", confirmed=True),)

    hot = analyse(make_snapshot(prices=spiky, benchmark=calm_bench, catalysts=cats), SETTINGS)
    cool = analyse(
        make_snapshot(prices=spiky, benchmark=spiky["adj_close"] * 0.5, catalysts=cats),
        SETTINGS,
    )

    assert hot.details["runup_excess_20d_pct"] is not None
    assert hot.details["runup_excess_20d_pct"] > 10.0
    assert hot.details["upcoming"][0]["priced_in_pct"] > 0.0
    assert cool.details["upcoming"][0]["priced_in_pct"] == 0.0
    priced = [e for e in hot.evidence if e.key == "catalyst_priced_in"]
    assert priced and priced[0].direction == "contradicts"
    assert "heuristic" in priced[0].statement.lower()
    assert hot.score is not None and cool.score is not None
    assert hot.score < cool.score


# ---------------------------------------------------------------------------
# Refinancing relevance (leverage-aware)
# ---------------------------------------------------------------------------


def test_refinancing_relevance_depends_on_leverage() -> None:
    cats = (catalyst(days_ahead=45, kind="refinancing", binary=False,
                     description="Notes maturity"),)
    levered = analyse(
        _flat_market_snapshot(
            catalysts=cats,
            fundamentals=quarterly_fundamentals(debt=5000e6, cash=100e6),
        ),
        SETTINGS,
    )
    unlevered = analyse(
        _flat_market_snapshot(
            catalysts=cats,
            fundamentals=quarterly_fundamentals(debt=100e6, cash=800e6),
        ),
        SETTINGS,
    )
    unknown = analyse(_flat_market_snapshot(catalysts=cats), SETTINGS)

    assert levered.details["upcoming"][0]["relevance"] == 1.0
    assert unlevered.details["upcoming"][0]["relevance"] == 0.5
    assert unknown.details["upcoming"][0]["relevance"] == 0.75
    assert any("leverage" in w for w in unknown.warnings)
    assert levered.components["medium_term"] > unlevered.components["medium_term"]


# ---------------------------------------------------------------------------
# Recent outcomes
# ---------------------------------------------------------------------------


def test_recent_positive_outcome_adds_tailwind_and_negative_headwind() -> None:
    positive = analyse(
        _flat_market_snapshot(
            catalysts=(_resolved(20, "earnings", "EPS surprise +6.2%"),),
        ),
        SETTINGS,
    )
    negative = analyse(
        _flat_market_snapshot(
            catalysts=(_resolved(20, "guidance", "FY guidance cut on weak demand"),),
        ),
        SETTINGS,
    )
    assert positive.components["recent_outcomes"] > 5.0
    assert negative.components["recent_outcomes"] < 5.0
    pos_ev = [e for e in positive.evidence if e.key.startswith("recent_outcome_")]
    neg_ev = [e for e in negative.evidence if e.key.startswith("recent_outcome_")]
    assert pos_ev and pos_ev[0].direction == "supports"
    assert neg_ev and neg_ev[0].direction == "contradicts"


def test_old_or_unclassifiable_outcomes_stay_neutral() -> None:
    old = analyse(
        _flat_market_snapshot(catalysts=(_resolved(200, "earnings", "EPS surprise +9.0%"),)),
        SETTINGS,
    )
    vague = analyse(
        _flat_market_snapshot(catalysts=(_resolved(20, "contract", "Terms as expected"),)),
        SETTINGS,
    )
    assert old.components["recent_outcomes"] == 5.0
    assert vague.components["recent_outcomes"] == 5.0
    assert vague.details["recent_outcome_count"] == 0


# ---------------------------------------------------------------------------
# Details contract
# ---------------------------------------------------------------------------


def test_details_contract_keys_and_types() -> None:
    result = analyse(_rich_snapshot(), SETTINGS)
    d = result.details

    assert isinstance(d["upcoming"], list) and d["upcoming"]
    for item in d["upcoming"]:
        assert set(item) == UPCOMING_ITEM_KEYS
        assert isinstance(item["kind"], str)
        assert isinstance(item["date"], str)
        assert isinstance(item["days"], int)
        assert isinstance(item["binary"], bool)
        assert isinstance(item["confirmed"], bool)
        assert isinstance(item["description"], str)
        assert item["priced_in_pct"] is None or (
            isinstance(item["priced_in_pct"], float) and 0.0 <= item["priced_in_pct"] <= 100.0
        )
        assert isinstance(item["relevance"], float)
    # sorted nearest-first
    days_list = [item["days"] for item in d["upcoming"]]
    assert days_list == sorted(days_list)

    assert isinstance(d["binary_event_within_20d"], bool)
    assert d["next_binary"] is None or set(d["next_binary"]) == {"kind", "date", "days"}
    assert d["next_earnings"] is not None
    assert set(d["next_earnings"]) == {"date", "days", "confirmed"}
    assert d["next_earnings"]["days"] == 12
    assert d["next_earnings"]["confirmed"] is True
    assert pd.to_datetime(d["next_earnings"]["date"]).date() == AS_OF + timedelta(days=12)


def test_resolved_and_far_future_catalysts_excluded_from_upcoming() -> None:
    snap = _flat_market_snapshot(
        catalysts=(
            catalyst(days_ahead=15, kind="earnings"),
            catalyst(days_ahead=3000, kind="product_launch"),  # beyond the long window
            _resolved(10, "guidance", "Guidance raised"),
        ),
    )
    result = analyse(snap, SETTINGS)
    assert [item["kind"] for item in result.details["upcoming"]] == ["earnings"]


def test_overdue_unresolved_catalyst_warned_and_excluded() -> None:
    snap = _flat_market_snapshot(catalysts=(catalyst(days_ahead=-5, kind="regulatory"),))
    result = analyse(snap, SETTINGS)
    assert result.details["upcoming"] == []
    assert any("past their expected date" in w for w in result.warnings)


def test_unconfirmed_date_contributes_less_than_confirmed() -> None:
    confirmed = analyse(
        _flat_market_snapshot(catalysts=(catalyst(days_ahead=15, confirmed=True),)),
        SETTINGS,
    )
    tentative = analyse(
        _flat_market_snapshot(catalysts=(catalyst(days_ahead=15, confirmed=False),)),
        SETTINGS,
    )
    assert confirmed.components["near_term"] > tentative.components["near_term"] > 5.0
    assert tentative.data_quality < confirmed.data_quality


def test_evidence_values_backed_by_sources() -> None:
    for snap in (
        _rich_snapshot(),
        _flat_market_snapshot(catalysts=(catalyst(days_ahead=5, binary=True),)),
    ):
        result = analyse(snap, SETTINGS)
        for item in result.evidence:
            if item.value is not None:
                assert item.source.provider
                assert item.source.reference


def test_scores_5ish_with_defaults_and_only_far_catalyst() -> None:
    result = analyse(
        _flat_market_snapshot(catalysts=(catalyst(days_ahead=400, kind="product_launch"),)),
        SETTINGS,
    )
    assert result.score is not None
    assert result.components["near_term"] == 5.0
    assert result.components["medium_term"] == 5.0
    assert result.components["long_term"] > 5.0
    assert 5.0 <= result.score < 5.5  # long horizon carries little weight
