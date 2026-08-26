"""Tests for the valuation engine (spec section 2 of ENGINE_SPEC.md)."""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest

from tests.factories import (
    AS_OF,
    catalyst,
    estimate,
    make_snapshot,
    price_frame,
    quarterly_fundamentals,
)
from vigil.config import Settings
from vigil.engines.valuation import analyse
from vigil.schemas.core import (
    InstrumentSnapshot,
    LiquidityStats,
    PeerMetrics,
    SourceRef,
    TargetRecord,
)


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None)


def _peers(pe: float = 24.0, growth: float = 0.05, gm: float = 0.45) -> tuple[PeerMetrics, ...]:
    out = []
    for i, mult in enumerate((0.8, 0.95, 1.1, 1.25)):
        out.append(
            PeerMetrics(
                instrument_id=100 + i,
                ticker=f"PEER{i}",
                name=f"Peer {i}",
                sector="Technology",
                industry="Software",
                metrics={
                    "pe_ttm": pe * mult,
                    "ev_sales": 4.0 * mult,
                    "pb": 3.0 * mult,
                    "fcf_yield": 0.04 / mult,
                    "gross_margin": gm,
                    "revenue_growth_ttm": growth,
                },
            )
        )
    return tuple(out)


def _target(
    mean: float, count: int = 12, std: float | None = None, age: float = 20.0
) -> TargetRecord:
    return TargetRecord(
        as_of=AS_OF - timedelta(days=3),
        currency="USD",
        mean=mean,
        high=mean * 1.15,
        low=mean * 0.85,
        std=std if std is not None else mean * 0.08,
        analyst_count=count,
        median_age_days=age,
        mean_30d_ago=mean * 0.98,
        source=SourceRef(
            provider="test-targets",
            source_type="target",
            reference="test://targets",
            published_at=datetime.combine(AS_OF - timedelta(days=3), time(9)),
        ),
    )


def cheap_quality_snapshot(**kwargs) -> InstrumentSnapshot:
    """Growing, cash-generative business whose price has drifted down ~30%:
    cheap absolutely, vs its own history and vs peers, with no trap flags."""
    defaults = dict(
        prices=price_frame(shape=[(0.0, 1.0, -0.15)]),
        fundamentals=quarterly_fundamentals(quarters=16),
        estimates=(estimate(mean=9.0, mean_30d_ago=8.7, mean_90d_ago=8.5),),
        peers=_peers(),
        catalysts=(catalyst(days_ahead=30),),
    )
    defaults.update(kwargs)
    return make_snapshot(**defaults)


def expensive_snapshot() -> InstrumentSnapshot:
    """Same fundamentals but the price has tripled: expensive everywhere."""
    return make_snapshot(
        prices=price_frame(shape=[(0.0, 1.0, 0.80)]),
        fundamentals=quarterly_fundamentals(quarters=16),
        estimates=(estimate(mean=9.0, mean_30d_ago=8.7, mean_90d_ago=8.5),),
        peers=_peers(),
        catalysts=(catalyst(days_ahead=30),),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_cheap_quality_scores_high(settings: Settings) -> None:
    res = analyse(cheap_quality_snapshot(), settings)
    assert res.engine == "valuation"
    assert res.score is not None and res.score >= 6.5
    assert set(res.components) == {"absolute", "vs_history", "vs_peers", "scenario_asymmetry"}
    assert all(0.0 <= v <= 10.0 for v in res.components.values())
    assert res.evidence, "expected non-empty evidence"
    assert 4 <= len(res.evidence) <= 12
    for item in res.evidence:
        if item.value is not None:
            assert item.source.provider
    assert res.details["value_trap"]["is_trap_risk"] is False


def test_expensive_name_scores_low(settings: Settings) -> None:
    cheap = analyse(cheap_quality_snapshot(), settings)
    rich = analyse(expensive_snapshot(), settings)
    assert rich.score is not None and cheap.score is not None
    assert rich.score < 4.5
    assert rich.score < cheap.score
    assert rich.components["vs_history"] < cheap.components["vs_history"]
    assert rich.components["vs_peers"] < cheap.components["vs_peers"]


def test_entry_zone_hint_below_price_when_attractive(settings: Settings) -> None:
    snap = cheap_quality_snapshot()
    res = analyse(snap, settings)
    hint = res.details["entry_zone_hint"]
    assert hint is not None
    assert hint["low"] < hint["high"] <= snap.last_close
    # An expensive name gets no accumulation band.
    rich = analyse(expensive_snapshot(), settings)
    assert rich.details["entry_zone_hint"] is None


# ---------------------------------------------------------------------------
# Value trap
# ---------------------------------------------------------------------------


def test_cheap_value_trap_is_capped_at_4(settings: Settings) -> None:
    snap = make_snapshot(
        prices=price_frame(start_price=30.0, shape=[(0.0, 1.0, -0.25)]),
        fundamentals=quarterly_fundamentals(
            quarters=16,
            revenue_growth_q=-0.04,  # structural decline
            cash_conversion=0.4,  # weak OCF/NI
            debt=6000e6,
            cash=100e6,  # heavy leverage
        ),
        estimates=(estimate(mean=4.0, mean_30d_ago=4.5, mean_90d_ago=5.0, up=0, down=6),),
        peers=_peers(),
        catalysts=(),  # nothing on the calendar
    )
    res = analyse(snap, settings)
    assert res.score is not None and res.score <= 4.0
    trap = res.details["value_trap"]
    assert trap["is_trap_risk"] is True
    assert len(trap["failed_checks"]) >= 2
    for expected in ("structural_revenue_decline", "weak_cash_conversion"):
        assert expected in trap["failed_checks"]
    assert any("capped" in w for w in res.warnings)
    assert any(e.key == "value_trap_checks" and e.direction == "contradicts" for e in res.evidence)


# ---------------------------------------------------------------------------
# Abstention
# ---------------------------------------------------------------------------


def test_abstains_with_fewer_than_4_quarters(settings: Settings) -> None:
    res = analyse(make_snapshot(fundamentals=quarterly_fundamentals(quarters=3)), settings)
    assert res.score is None
    assert res.warnings and "quarterly" in res.warnings[0]


def test_abstains_when_market_cap_unknown(settings: Settings) -> None:
    snap = make_snapshot(
        fundamentals=quarterly_fundamentals(quarters=16),
        shares=None,  # type: ignore[arg-type]
        liquidity=LiquidityStats(),
    )
    res = analyse(snap, settings)
    assert res.score is None
    assert any("market cap" in w for w in res.warnings)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_determinism(settings: Settings) -> None:
    snap = cheap_quality_snapshot(target=_target(mean=90.0))
    assert analyse(snap, settings).model_dump() == analyse(snap, settings).model_dump()


# ---------------------------------------------------------------------------
# Sector-aware behaviour
# ---------------------------------------------------------------------------


def test_reit_uses_p_ffo_as_primary(settings: Settings) -> None:
    ffo_overrides = {i: {"sector_metrics": {"ffo": 180e6}} for i in range(16)}
    snap = make_snapshot(
        sector="Real Estate",
        industry="Retail REITs",
        fundamentals=quarterly_fundamentals(quarters=16, overrides=ffo_overrides),
        catalysts=(catalyst(days_ahead=30),),
    )
    res = analyse(snap, settings)
    assert res.score is not None
    assert res.details["primary_multiple"] == "p_ffo"
    assert res.details["multiples"]["p_ffo"] is not None
    assert res.details["sector_class"] == "reit"


def test_bank_uses_p_tbv_as_primary(settings: Settings) -> None:
    snap = make_snapshot(
        sector="Financials",
        industry="Regional Banks",
        fundamentals=quarterly_fundamentals(quarters=16),
        catalysts=(catalyst(days_ahead=30),),
    )
    res = analyse(snap, settings)
    assert res.score is not None
    assert res.details["primary_multiple"] == "p_tbv"
    assert res.details["sector_class"] == "bank"


# ---------------------------------------------------------------------------
# Analyst targets: evidence only, never in fair value
# ---------------------------------------------------------------------------


def test_targets_are_evidence_only(settings: Settings) -> None:
    without = analyse(cheap_quality_snapshot(), settings)
    with_t = analyse(cheap_quality_snapshot(target=_target(mean=120.0)), settings)
    assert with_t.score == without.score
    assert with_t.components == without.components
    assert with_t.details["scenarios"] == without.details["scenarios"]
    assert without.details["target_summary"] is None
    summary = with_t.details["target_summary"]
    assert summary is not None
    for key in ("mean", "implied_upside_pct", "count", "dispersion_pct", "median_age_days"):
        assert key in summary
    assert any(e.key == "target_implied_upside" for e in with_t.evidence)


def test_unreliable_target_warns_and_contradicts(settings: Settings) -> None:
    snap = cheap_quality_snapshot(target=_target(mean=120.0, count=3, std=40.0))
    res = analyse(snap, settings)
    assert any("unreliable" in w for w in res.warnings)
    rel = [e for e in res.evidence if e.key == "target_reliability"]
    assert rel and rel[0].direction == "contradicts"
    upside = [e for e in res.evidence if e.key == "target_implied_upside"]
    assert upside and upside[0].direction == "neutral"


# ---------------------------------------------------------------------------
# Details contract
# ---------------------------------------------------------------------------


def test_details_contract(settings: Settings) -> None:
    res = analyse(cheap_quality_snapshot(target=_target(mean=90.0)), settings)
    d = res.details
    for key in (
        "scenarios",
        "fair_value_low",
        "fair_value_high",
        "primary_multiple",
        "multiples",
        "value_trap",
        "target_summary",
        "entry_zone_hint",
    ):
        assert key in d
    assert isinstance(d["primary_multiple"], str)
    assert isinstance(d["multiples"], dict)
    scen = d["scenarios"]
    assert scen is not None
    for name in ("base", "bull", "bear"):
        assert isinstance(scen[name]["price"], float)
        assert scen[name]["price"] == round(scen[name]["price"], 2)
        assert isinstance(scen[name]["rationale"], str) and scen[name]["rationale"]
    assert scen["bear"]["price"] <= scen["base"]["price"] <= scen["bull"]["price"]
    assert isinstance(d["fair_value_low"], float) and isinstance(d["fair_value_high"], float)
    assert d["fair_value_low"] <= d["fair_value_high"]
    trap = d["value_trap"]
    assert isinstance(trap["is_trap_risk"], bool)
    assert isinstance(trap["failed_checks"], list)
    assert 0.0 <= res.data_quality <= 1.0


def test_ev_multiple_is_labelled_ebit_not_ebitda(settings: Settings) -> None:
    res = analyse(cheap_quality_snapshot(), settings)
    assert "ev_ebit" in res.details["multiples"]
    assert "ev_ebitda" not in res.details["multiples"]
