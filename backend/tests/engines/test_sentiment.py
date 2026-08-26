"""Tests for the deterministic news-narrative sentiment engine."""

from __future__ import annotations

import pandas as pd
import pytest

from tests.factories import make_snapshot, news_item, price_frame
from vigil.config import Settings
from vigil.engines.sentiment import analyse
from vigil.schemas.core import InstrumentSnapshot, NewsRecord

SETTINGS = Settings()

COMPONENT_KEYS = {"direction", "momentum", "agreement", "confirmation"}
DETAIL_KEYS = {
    "direction_score", "rate_of_change", "disagreement", "price_confirms",
    "share_by_type", "contrarian_candidate", "item_count", "weighted_volume",
}
TYPE_KEYS = {
    "factual_event", "analyst_opinion", "management_claim", "market_commentary", "social",
}


def _rising_prices() -> pd.DataFrame:
    # Strong deterministic drift in the final segment: 1m return safely positive.
    return price_frame(days=400, shape=[(0.0, 0.9, 0.0), (0.9, 1.0, 2.5)],
                       daily_vol=0.008, seed=21)


def _falling_prices() -> pd.DataFrame:
    return price_frame(days=400, shape=[(0.0, 0.9, 0.0), (0.9, 1.0, -2.5)],
                       daily_vol=0.008, seed=22)


def _improving_news(scale: float = 1.0) -> tuple[NewsRecord, ...]:
    """Prior-window items mildly positive, recent items strongly positive."""
    return (
        news_item(80, 0.15 * scale, "analyst_opinion", headline="Initiated at hold"),
        news_item(60, 0.20 * scale, "factual_event", headline="Contract renewal signed"),
        news_item(45, 0.25 * scale, "market_commentary", headline="Sector strength noted"),
        news_item(20, 0.70 * scale, "factual_event", headline="Record quarterly revenue"),
        news_item(10, 0.75 * scale, "analyst_opinion", headline="Upgraded to buy"),
        news_item(3, 0.80 * scale, "factual_event", headline="Guidance raised"),
    )


def _deteriorating_news() -> tuple[NewsRecord, ...]:
    return (
        news_item(80, -0.10, "analyst_opinion", headline="Initiated at hold"),
        news_item(60, -0.20, "factual_event", headline="Contract loss disclosed"),
        news_item(45, -0.30, "market_commentary", headline="Sector weakness noted"),
        news_item(20, -0.70, "factual_event", headline="Revenue miss"),
        news_item(10, -0.75, "analyst_opinion", headline="Downgraded to sell"),
        news_item(3, -0.80, "factual_event", headline="Guidance cut"),
    )


def _positive_snapshot() -> InstrumentSnapshot:
    return make_snapshot(prices=_rising_prices(), news=_improving_news())


def _negative_snapshot() -> InstrumentSnapshot:
    return make_snapshot(prices=_falling_prices(), news=_deteriorating_news())


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_positive_narrative_scores_high_and_negative_low() -> None:
    positive = analyse(_positive_snapshot(), SETTINGS)
    negative = analyse(_negative_snapshot(), SETTINGS)

    assert positive.score is not None and negative.score is not None
    assert positive.score > 6.5
    assert negative.score < 3.5
    assert positive.score > negative.score + 3.0

    for result in (positive, negative):
        assert len(result.evidence) >= 4
        for item in result.evidence:
            assert item.source.provider, f"evidence {item.key} lacks a provider"
            assert item.statement
    assert any(e.direction == "supports" for e in positive.evidence)
    assert any(e.direction == "contradicts" for e in negative.evidence)


def test_score_and_components_within_bounds() -> None:
    for snap in (_positive_snapshot(), _negative_snapshot()):
        result = analyse(snap, SETTINGS)
        assert result.score is not None and 0.0 <= result.score <= 10.0
        assert set(result.components) == COMPONENT_KEYS
        for name, value in result.components.items():
            assert 0.0 <= value <= 10.0, f"component {name} out of bounds: {value}"
        assert 0.0 <= result.data_quality <= 1.0


# ---------------------------------------------------------------------------
# Abstention & determinism
# ---------------------------------------------------------------------------


def test_abstains_with_no_news_at_all() -> None:
    result = analyse(make_snapshot(news=()), SETTINGS)
    assert result.engine == "sentiment"
    assert result.score is None
    assert result.data_quality == 0.0
    assert result.warnings, "abstention must carry a reason"


def test_abstains_when_all_news_is_future_dated() -> None:
    # news_item with negative days_ago publishes after as_of: unusable.
    result = analyse(make_snapshot(news=(news_item(-5, 0.9),)), SETTINGS)
    assert result.score is None
    assert result.warnings


def test_determinism_same_snapshot_identical_result() -> None:
    snap = _positive_snapshot()
    assert analyse(snap, SETTINGS).model_dump() == analyse(snap, SETTINGS).model_dump()


# ---------------------------------------------------------------------------
# Weighting behaviour
# ---------------------------------------------------------------------------


def test_source_type_weights_factual_dominates_social() -> None:
    news = (
        news_item(5, 0.8, "factual_event", headline="Major contract win"),
        news_item(5, -0.8, "social", headline="Bearish thread"),
    )
    result = analyse(make_snapshot(news=news), SETTINGS)
    # factual (w=1.0) outweighs social (w=0.15): direction stays positive.
    assert result.details["direction_score"] > 0.5


def test_time_decay_recent_dominates_stale() -> None:
    news = (
        news_item(2, 0.8, "factual_event", headline="Fresh good news"),
        news_item(100, -0.8, "factual_event", headline="Stale bad news"),
    )
    result = analyse(make_snapshot(news=news), SETTINGS)
    # 100d decay = 0.5^(100/21) ~= 0.037 vs 0.5^(2/21) ~= 0.94.
    assert result.details["direction_score"] > 0.5


def test_novelty_scales_weight() -> None:
    novel = analyse(
        make_snapshot(news=(news_item(5, 0.8, novelty=1.0),)), SETTINGS
    )
    repeat = analyse(
        make_snapshot(news=(news_item(5, 0.8, novelty=0.2),)), SETTINGS
    )
    assert novel.details["weighted_volume"] > repeat.details["weighted_volume"]


# ---------------------------------------------------------------------------
# Price confirmation
# ---------------------------------------------------------------------------


def test_price_confirmation_states() -> None:
    confirmed = analyse(
        make_snapshot(prices=_rising_prices(), news=_improving_news()), SETTINGS
    )
    contradicted = analyse(
        make_snapshot(prices=_falling_prices(), news=_improving_news()), SETTINGS
    )
    neutral_news = tuple(
        news_item(d, 0.0, "market_commentary", headline=f"Note {d}") for d in (3, 10, 20)
    )
    unconfirmed = analyse(
        make_snapshot(prices=_rising_prices(), news=neutral_news), SETTINGS
    )
    assert confirmed.details["price_confirms"] == "confirmed"
    assert contradicted.details["price_confirms"] == "contradicted"
    assert unconfirmed.details["price_confirms"] == "unconfirmed"
    assert confirmed.score is not None and contradicted.score is not None
    assert confirmed.score > contradicted.score
    # negative narrative with price breakdown is confirmed too - and scores low
    breakdown = analyse(
        make_snapshot(prices=_falling_prices(), news=_deteriorating_news()), SETTINGS
    )
    assert breakdown.details["price_confirms"] == "confirmed"
    assert breakdown.score is not None and breakdown.score < 3.5


# ---------------------------------------------------------------------------
# Penalties / caps / flags
# ---------------------------------------------------------------------------


def test_social_heavy_flow_caps_score_at_6() -> None:
    social = tuple(
        news_item(d, 0.9, "social", headline=f"Moon post {d}") for d in range(1, 11)
    )
    anchor = (news_item(4, 0.9, "factual_event", headline="Earnings beat"),)
    result = analyse(
        make_snapshot(prices=_rising_prices(), news=social + anchor), SETTINGS
    )
    assert result.details["share_by_type"]["social"] > 0.40
    assert result.score is not None and result.score <= 6.0
    assert any("social" in w for w in result.warnings)
    capped = [e for e in result.evidence if e.key == "social_heavy_flow"]
    assert capped and capped[0].direction == "contradicts"


def test_contrarian_candidate_flagged_only_on_upturn() -> None:
    turning = (
        news_item(80, -0.9, "factual_event", headline="Profit warning"),
        news_item(60, -0.9, "analyst_opinion", headline="Downgraded"),
        news_item(45, -0.85, "factual_event", headline="CFO exit"),
        news_item(10, -0.35, "factual_event", headline="Cost plan on track"),
        news_item(3, -0.30, "analyst_opinion", headline="Worst may be over"),
    )
    still_falling = (
        news_item(80, -0.4, "factual_event", headline="Profit warning"),
        news_item(60, -0.5, "analyst_opinion", headline="Downgraded"),
        news_item(45, -0.6, "factual_event", headline="CFO exit"),
        news_item(10, -0.85, "factual_event", headline="Covenant breach"),
        news_item(3, -0.9, "analyst_opinion", headline="Sell"),
    )
    flagged = analyse(make_snapshot(prices=_falling_prices(), news=turning), SETTINGS)
    unflagged = analyse(make_snapshot(prices=_falling_prices(), news=still_falling), SETTINGS)
    assert flagged.details["contrarian_candidate"] is True
    assert unflagged.details["contrarian_candidate"] is False
    contrarian = [e for e in flagged.evidence if e.key == "contrarian_candidate"]
    assert contrarian and contrarian[0].direction == "supports"
    assert flagged.score is not None and unflagged.score is not None
    assert flagged.score > unflagged.score


def test_management_vs_analyst_divergence_raises_disagreement() -> None:
    divergent = (
        news_item(5, 0.9, "management_claim", headline="CEO: best year ever"),
        news_item(6, 0.85, "management_claim", headline="CFO: demand is strong"),
        news_item(4, -0.7, "analyst_opinion", headline="Downgraded on channel checks"),
        news_item(8, -0.6, "analyst_opinion", headline="Estimates look stale"),
    )
    aligned = (
        news_item(5, 0.3, "management_claim", headline="CEO: steady progress"),
        news_item(6, 0.3, "management_claim", headline="CFO: on plan"),
        news_item(4, 0.3, "analyst_opinion", headline="Maintained at hold"),
        news_item(8, 0.3, "analyst_opinion", headline="Fairly valued"),
    )
    d_result = analyse(make_snapshot(news=divergent), SETTINGS)
    a_result = analyse(make_snapshot(news=aligned), SETTINGS)
    assert d_result.details["disagreement"] is not None
    assert a_result.details["disagreement"] is not None
    assert d_result.details["disagreement"] > a_result.details["disagreement"]
    gap = [e for e in d_result.evidence if e.key == "mgmt_analyst_divergence"]
    assert gap and gap[0].direction == "contradicts"


def test_thin_flow_shrinks_score_toward_neutral() -> None:
    thin = analyse(
        make_snapshot(news=(news_item(40, 0.9, "social", headline="One old post"),)),
        SETTINGS,
    )
    assert thin.score is not None
    assert 4.0 <= thin.score <= 6.0
    assert any("thin news flow" in w for w in thin.warnings)


# ---------------------------------------------------------------------------
# Details contract
# ---------------------------------------------------------------------------


def test_details_contract_keys_and_types() -> None:
    for snap in (_positive_snapshot(), _negative_snapshot()):
        d = analyse(snap, SETTINGS).details
        assert set(d) == DETAIL_KEYS
        assert isinstance(d["direction_score"], float)
        assert -1.0 <= d["direction_score"] <= 1.0
        assert d["rate_of_change"] is None or isinstance(d["rate_of_change"], float)
        assert d["disagreement"] is None or isinstance(d["disagreement"], float)
        assert d["price_confirms"] in {"confirmed", "unconfirmed", "contradicted"}
        assert set(d["share_by_type"]) == TYPE_KEYS
        assert all(isinstance(v, float) for v in d["share_by_type"].values())
        assert sum(d["share_by_type"].values()) == pytest.approx(1.0, abs=0.01)
        assert isinstance(d["contrarian_candidate"], bool)
        assert isinstance(d["item_count"], int)
        assert isinstance(d["weighted_volume"], float)


def test_rate_of_change_none_when_prior_window_empty() -> None:
    recent_only = tuple(
        news_item(d, 0.5, "factual_event", headline=f"Update {d}") for d in (2, 5, 9)
    )
    result = analyse(make_snapshot(news=recent_only), SETTINGS)
    assert result.details["rate_of_change"] is None
    assert result.score is not None  # direction still measurable
    assert any("rate of change" in w for w in result.warnings)
