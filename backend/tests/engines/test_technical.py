"""Tests for the technical engine (spec section 3 of ENGINE_SPEC.md)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tests.factories import AS_OF, catalyst, make_snapshot, price_frame
from vigil.config import Settings
from vigil.engines.technical import analyse
from vigil.schemas.core import InstrumentSnapshot


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None)


def uptrend_snapshot(**kwargs) -> InstrumentSnapshot:
    """Steady +30%/year trend: stacked MAs, positive slopes, decent RS."""
    defaults = dict(prices=price_frame(shape=[(0.0, 1.0, 0.30)], daily_vol=0.012))
    defaults.update(kwargs)
    return make_snapshot(**defaults)


def downtrend_snapshot() -> InstrumentSnapshot:
    """Persistent -50%/year decline: price under falling MAs, weak RS."""
    return make_snapshot(prices=price_frame(shape=[(0.0, 1.0, -0.50)], daily_vol=0.01))


def breakout_frame() -> pd.DataFrame:
    """Deterministic path: a rise to 150, a long 140-150 triangle
    consolidation, then a 10-bar breakout to 154.5 on doubled volume —
    strong enough to clear the range, gentle enough not to be parabolic."""
    rise = np.linspace(100.0, 150.0, 150)
    leg_down = np.linspace(150.0, 140.0, 11)[1:]
    leg_up = np.linspace(140.0, 150.0, 11)[1:]
    osc = np.tile(np.concatenate([leg_down, leg_up]), 9)  # 180 bars, ends at 150
    ramp = np.linspace(150.45, 154.5, 10)
    close = np.concatenate([rise, osc, ramp])
    n = len(close)
    opens = np.empty(n)
    opens[0] = close[0]
    opens[1:] = close[:-1]
    high = np.maximum(opens, close) * 1.008
    low = np.minimum(opens, close) * 0.992
    volume = np.full(n, 1e6)
    volume[-10:] = 2e6
    idx = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=n)
    return pd.DataFrame(
        {"open": opens, "high": high, "low": low, "close": close,
         "adj_close": close, "adj_open": opens, "volume": volume},
        index=idx,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_uptrend_scores_above_downtrend(settings: Settings) -> None:
    up = analyse(uptrend_snapshot(), settings)
    down = analyse(downtrend_snapshot(), settings)
    assert up.engine == "technical"
    assert up.score is not None and down.score is not None
    assert up.score >= 6.0
    assert down.score < 5.0
    assert up.score > down.score
    assert set(up.components) == {"trend", "setup", "confirmation"}
    assert all(0.0 <= v <= 10.0 for v in up.components.values())
    assert up.components["trend"] >= 6.5
    assert down.components["trend"] <= 4.0
    assert up.details["trend_state"] == "uptrend"
    assert up.details["above_sma200"] is True
    assert 4 <= len(up.evidence) <= 12
    for res in (up, down):
        for item in res.evidence:
            if item.value is not None:
                assert item.source.provider
                assert item.source.source_type in ("derived", "price")


def test_confirmed_breakout_is_a_strong_setup(settings: Settings) -> None:
    res = analyse(make_snapshot(prices=breakout_frame()), settings)
    assert res.score is not None and res.score >= 6.5
    assert res.details["breakout"]["state"] == "breakout"
    assert res.details["breakout"]["volume_ratio"] >= 1.3
    assert res.details["setup_kind"] == "breakout"
    assert res.details["extended"] is False
    assert res.components["setup"] >= 7.5
    hint = res.details["entry_zone_hint"]
    assert hint is not None and hint["low"] <= hint["high"]
    assert hint["low"] == pytest.approx(res.details["breakout"]["range_high"], rel=1e-3)
    assert any(e.key == "breakout_state" and e.direction == "supports" for e in res.evidence)
    nearest = res.details["nearest_support"]
    assert nearest is not None
    assert res.details["stop_hint"] is not None
    assert res.details["stop_hint"] < nearest["low"]  # zone low minus 1x ATR


# ---------------------------------------------------------------------------
# Penalties / edge cases
# ---------------------------------------------------------------------------


def test_parabolic_extension_is_penalised(settings: Settings) -> None:
    snap = make_snapshot(prices=price_frame(shape=[(0.0, 0.9, 0.15), (0.9, 1.0, 3.0)]))
    res = analyse(snap, settings)
    assert res.score is not None
    assert res.details["extended"] is True
    assert "parabolic_extension" in res.details["penalties"]
    assert any("parabolic" in w for w in res.warnings)
    assert any(
        e.key == "parabolic_extension" and e.direction == "contradicts" for e in res.evidence
    )
    # An extended price is not an entrable setup: capped and no entry zone.
    assert res.components["setup"] <= 5.5
    assert res.details["entry_zone_hint"] is None


def test_oversold_rsi_alone_adds_at_most_one_point(settings: Settings) -> None:
    snap = make_snapshot(
        prices=price_frame(shape=[(0.0, 0.9, 0.10), (0.9, 1.0, -3.0)], daily_vol=0.012)
    )
    res = analyse(snap, settings)
    assert res.score is not None
    assert res.details["rsi14"] is not None and res.details["rsi14"] < 30.0
    assert res.details["setup_kind"] == "none"
    assert res.components["setup"] <= 6.0  # neutral 5 + at most 1 for oversold RSI


def test_degenerate_volume_warns_but_still_scores(settings: Settings) -> None:
    res = analyse(make_snapshot(prices=price_frame(volume=0.0)), settings)
    assert res.score is not None
    assert any("volume" in w for w in res.warnings)
    assert res.data_quality < 1.0
    assert res.details["anchored_vwap"] is None  # no volume, no VWAP — never invented


def test_anchored_vwap_uses_latest_past_earnings_event(settings: Settings) -> None:
    plain = analyse(uptrend_snapshot(), settings)
    anchored = analyse(
        uptrend_snapshot(catalysts=(catalyst(days_ahead=-30, kind="earnings"),)), settings
    )
    assert plain.details["anchored_vwap"] is not None
    assert anchored.details["anchored_vwap"] is not None
    # In a rising market, anchoring 30 days ago (vs 6 months) raises the VWAP.
    assert anchored.details["anchored_vwap"] > plain.details["anchored_vwap"]


# ---------------------------------------------------------------------------
# Abstention
# ---------------------------------------------------------------------------


def test_abstains_with_fewer_than_120_bars(settings: Settings) -> None:
    res = analyse(make_snapshot(prices=price_frame(days=100)), settings)
    assert res.score is None
    assert res.warnings and "bars" in res.warnings[0]
    assert res.evidence == []


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_determinism(settings: Settings) -> None:
    snap = uptrend_snapshot(catalysts=(catalyst(days_ahead=-30, kind="earnings"),))
    assert analyse(snap, settings).model_dump() == analyse(snap, settings).model_dump()


# ---------------------------------------------------------------------------
# Details contract
# ---------------------------------------------------------------------------


def test_details_contract(settings: Settings) -> None:
    res = analyse(make_snapshot(prices=breakout_frame()), settings)
    d = res.details
    for key in (
        "support_zones",
        "resistance_levels",
        "nearest_support",
        "stop_hint",
        "reward_risk",
        "entry_zone_hint",
        "breakout",
        "trend_state",
        "extended",
        "atr_pct",
        "realised_vol_annual",
        "drawdown_from_52w_high_pct",
        "rsi14",
        "above_sma200",
        "anchored_vwap",
        "rs_3m_market",
    ):
        assert key in d
    assert isinstance(d["support_zones"], list)
    for zone in d["support_zones"]:
        assert set(zone) == {"low", "high", "strength", "basis"}
        assert zone["low"] <= zone["high"]
    assert isinstance(d["resistance_levels"], list)
    assert all(isinstance(r, float) for r in d["resistance_levels"])
    if d["nearest_support"] is not None:
        assert d["nearest_support"]["low"] <= d["nearest_support"]["high"]
    assert d["stop_hint"] is None or isinstance(d["stop_hint"], float)
    assert d["reward_risk"] is None or 0.0 <= d["reward_risk"] <= 5.0
    assert d["entry_zone_hint"] is None or d["entry_zone_hint"]["low"] <= d["entry_zone_hint"]["high"]
    assert isinstance(d["breakout"], dict) and "state" in d["breakout"]
    assert d["trend_state"] in ("uptrend", "downtrend", "range")
    assert isinstance(d["extended"], bool)
    assert isinstance(d["atr_pct"], float) and d["atr_pct"] > 0
    assert isinstance(d["realised_vol_annual"], float) and d["realised_vol_annual"] > 0
    assert isinstance(d["drawdown_from_52w_high_pct"], float)
    assert d["rsi14"] is not None and 0.0 <= d["rsi14"] <= 100.0
    assert isinstance(d["above_sma200"], bool)
    assert d["anchored_vwap"] is None or isinstance(d["anchored_vwap"], float)
    assert d["rs_3m_market"] is None or isinstance(d["rs_3m_market"], float)
    assert 0.0 <= res.data_quality <= 1.0
