"""Tests for the sector-aware quality engine."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from tests.factories import AS_OF, make_snapshot, quarterly_fundamentals
from vigil.config import Settings
from vigil.engines.quality import analyse
from vigil.schemas.core import InsiderRecord, InstrumentSnapshot, SourceRef

SETTINGS = Settings()

COMPONENT_KEYS = {"quality", "growth", "balance_sheet", "cash_quality", "shareholder"}
VALUE_TRAP_KEYS = {
    "structural_revenue_decline", "margin_collapse", "excess_leverage",
    "dilution", "weak_cash_conversion", "governance_flags",
}


def _strong_snapshot() -> InstrumentSnapshot:
    funds = quarterly_fundamentals(
        quarters=16, revenue_growth_q=0.03, gross_margin=0.62, op_margin=0.26,
        net_margin=0.19, cash_conversion=1.15, capex_pct=0.04,
        debt=200e6, cash=900e6,
    )
    return make_snapshot(fundamentals=funds)


def _weak_snapshot() -> InstrumentSnapshot:
    funds = quarterly_fundamentals(
        quarters=16, revenue_growth_q=-0.03, gross_margin=0.30, op_margin=0.05,
        net_margin=0.02, cash_conversion=0.45, capex_pct=0.05,
        debt=4000e6, cash=100e6,
        overrides={
            i: (
                {"debt_due_within_1y": 2000e6, "receivables": 700e6}
                if i == 15
                else {"debt_due_within_1y": 2000e6}
            )
            for i in range(16)
        },
    )
    return make_snapshot(fundamentals=funds)


def _insider(days_ago: int, kind: str, name: str) -> InsiderRecord:
    when = AS_OF - timedelta(days=days_ago)
    return InsiderRecord(
        filed_at=datetime.combine(when, time(18)),
        transaction_date=when,
        insider_name=name,
        insider_role="Director",
        kind=kind,  # type: ignore[arg-type]
        shares=10_000,
        value=500_000.0,
        source=SourceRef(provider="test", source_type="insider", reference="test://insider"),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_strong_fundamentals_score_high_and_weak_score_low() -> None:
    strong = analyse(_strong_snapshot(), SETTINGS)
    weak = analyse(_weak_snapshot(), SETTINGS)

    assert strong.score is not None and weak.score is not None
    assert strong.score > 6.5
    assert weak.score < 3.5
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
    result = analyse(_strong_snapshot(), SETTINGS)
    assert result.score is not None and 0.0 <= result.score <= 10.0
    assert set(result.components) == COMPONENT_KEYS
    for name, value in result.components.items():
        assert 0.0 <= value <= 10.0, f"component {name} out of bounds: {value}"
    assert 0.0 <= result.data_quality <= 1.0


# ---------------------------------------------------------------------------
# Abstention & determinism
# ---------------------------------------------------------------------------


def test_abstains_with_fewer_than_four_quarters() -> None:
    snap = make_snapshot(fundamentals=quarterly_fundamentals(quarters=3))
    result = analyse(snap, SETTINGS)
    assert result.score is None
    assert result.warnings, "abstention must carry a reason"
    assert result.engine == "quality"


def test_abstains_with_no_fundamentals_at_all() -> None:
    snap = make_snapshot(fundamentals=())
    result = analyse(snap, SETTINGS)
    assert result.score is None
    assert result.warnings


def test_determinism_same_snapshot_identical_result() -> None:
    snap = _weak_snapshot()
    first = analyse(snap, SETTINGS)
    second = analyse(snap, SETTINGS)
    assert first.model_dump() == second.model_dump()


# ---------------------------------------------------------------------------
# Sector-aware behaviour
# ---------------------------------------------------------------------------


def _bank_fundamentals(quarters: int = 12):
    sm = {
        "net_interest_margin": 0.032,
        "cet1_ratio": 0.13,
        "loan_loss_provisions": 30e6,
        "tangible_book_per_share": 18.0,
    }
    return quarterly_fundamentals(
        quarters=quarters, op_margin=0.42, net_margin=0.22,
        debt=20_000e6, cash=3_000e6,
        overrides={i: {"sector_metrics": dict(sm), "capex": 5e6} for i in range(quarters)},
    )


def test_bank_uses_capital_adequacy_not_leverage() -> None:
    bank_snap = make_snapshot(
        sector="Financials", industry="Regional Banks", fundamentals=_bank_fundamentals()
    )
    bank = analyse(bank_snap, SETTINGS)
    assert bank.details["sector_class"] == "bank"
    assert bank.details["net_debt_ebitda"] is None
    assert bank.details["interest_coverage"] is None
    assert bank.details["refinancing_risk"] is False
    assert bank.score is not None and bank.score >= 5.0

    # The identical (heavily indebted) financials scored on the general path
    # must be punished for leverage — sector-awareness is the difference.
    general_snap = make_snapshot(
        sector="Technology", industry="Software", fundamentals=_bank_fundamentals()
    )
    general = analyse(general_snap, SETTINGS)
    assert general.details["sector_class"] == "general"
    assert general.score is not None
    assert bank.score > general.score
    assert bank.components["balance_sheet"] > general.components["balance_sheet"]
    assert any(e.key == "cet1_ratio" for e in bank.evidence)


def test_early_stage_caps_score_and_warns() -> None:
    funds = quarterly_fundamentals(
        quarters=8, revenue0=40e6, revenue_growth_q=0.08,
        op_margin=-0.20, net_margin=-0.25, cash_conversion=1.0,
        debt=10e6, cash=400e6,
    )
    snap = make_snapshot(fundamentals=funds)
    result = analyse(snap, SETTINGS)
    assert result.details["sector_class"] == "early_stage"
    assert result.score is not None and result.score <= 6.0
    assert any("early-stage" in w or "pre-profit" in w for w in result.warnings)


def test_reit_scores_ltv_and_ffo() -> None:
    sm = {"ffo": 120e6, "ffo_per_share": 1.2, "occupancy": 0.95, "ltv": 0.30}
    funds = quarterly_fundamentals(
        quarters=12, op_margin=0.38, net_margin=0.25, cash_conversion=1.3,
        debt=5_000e6, cash=200e6,
        overrides={i: {"sector_metrics": dict(sm)} for i in range(12)},
    )
    snap = make_snapshot(sector="Real Estate", industry="Industrial REITs", fundamentals=funds)
    result = analyse(snap, SETTINGS)
    assert result.details["sector_class"] == "reit"
    assert result.score is not None and result.score >= 5.0  # high debt, low LTV: not punished
    assert any(e.key == "ltv" for e in result.evidence)
    assert any(e.key == "occupancy" for e in result.evidence)


# ---------------------------------------------------------------------------
# Red flags & value-trap inputs
# ---------------------------------------------------------------------------


def test_red_flags_and_value_trap_inputs_on_weak_company() -> None:
    result = analyse(_weak_snapshot(), SETTINGS)
    flags = result.details["red_flags"]
    assert isinstance(flags, list) and flags
    assert any("cash conversion" in f for f in flags), flags
    assert any("receivables" in f for f in flags), flags

    vt = result.details["value_trap_inputs"]
    assert vt["weak_cash_conversion"] is True
    assert vt["excess_leverage"] is True
    assert vt["structural_revenue_decline"] is True
    assert result.details["refinancing_risk"] is True
    assert result.details["net_debt_ebitda"] is not None
    assert result.details["net_debt_ebitda"] > 4.0

    red_flag_evidence = [e for e in result.evidence if e.key.startswith("red_flag_")]
    assert red_flag_evidence
    assert all(e.direction == "contradicts" for e in red_flag_evidence)
    assert any("red flag" in w.lower() for w in result.warnings)


def test_clean_company_has_no_red_flags() -> None:
    result = analyse(_strong_snapshot(), SETTINGS)
    assert result.details["red_flags"] == []
    vt = result.details["value_trap_inputs"]
    assert not any(vt.values())


def test_insider_cluster_buys_lift_shareholder_component() -> None:
    base = analyse(_strong_snapshot(), SETTINGS)
    funds = quarterly_fundamentals(
        quarters=16, revenue_growth_q=0.03, gross_margin=0.62, op_margin=0.26,
        net_margin=0.19, cash_conversion=1.15, capex_pct=0.04,
        debt=200e6, cash=900e6,
    )
    with_buys = make_snapshot(
        fundamentals=funds,
        insiders=(
            _insider(20, "buy", "CEO"),
            _insider(25, "buy", "CFO"),
            _insider(40, "buy", "Chair"),
        ),
    )
    boosted = analyse(with_buys, SETTINGS)
    assert boosted.components["shareholder"] > base.components["shareholder"]
    assert any(e.key == "insider_cluster_buys" and e.direction == "supports"
               for e in boosted.evidence)


# ---------------------------------------------------------------------------
# Details contract
# ---------------------------------------------------------------------------


def test_details_contract_keys_and_types() -> None:
    for snap in (_strong_snapshot(), _weak_snapshot()):
        result = analyse(snap, SETTINGS)
        d = result.details

        assert d["sector_class"] in {
            "general", "bank", "insurer", "reit", "commodity", "early_stage"
        }
        assert isinstance(d["red_flags"], list)
        assert all(isinstance(f, str) for f in d["red_flags"])
        assert set(d["value_trap_inputs"]) == VALUE_TRAP_KEYS
        assert all(isinstance(v, bool) for v in d["value_trap_inputs"].values())
        assert set(d["growth_metrics"]) == {"revenue_cagr_3y", "eps_cagr_3y", "fcf_cagr_3y"}
        for v in d["growth_metrics"].values():
            assert v is None or isinstance(v, float)
        assert d["net_debt_ebitda"] is None or isinstance(d["net_debt_ebitda"], float)
        assert d["interest_coverage"] is None or isinstance(d["interest_coverage"], float)
        assert isinstance(d["refinancing_risk"], bool)
        assert d["dilution_pct_1y"] is None or isinstance(d["dilution_pct_1y"], float)


def test_growth_metrics_populated_with_16_quarters() -> None:
    result = analyse(_strong_snapshot(), SETTINGS)
    gm = result.details["growth_metrics"]
    assert gm["revenue_cagr_3y"] is not None and gm["revenue_cagr_3y"] > 0
    assert gm["eps_cagr_3y"] is not None
    assert gm["fcf_cagr_3y"] is not None
