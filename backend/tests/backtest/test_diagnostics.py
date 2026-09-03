"""A zero-trade backtest must explain itself (abstention is designed, not mute)."""

from __future__ import annotations

from vigil.backtest.engine import zero_trade_notes
from vigil.config import get_settings


def test_sparse_data_explanation_names_the_gate_and_the_fix():
    settings = get_settings()
    diag = {
        "snapshot_failures": 0,
        "ineligible": 12,
        "watch_candidates": 62,
        "triggered_candidates": 0,
        "unfilled_entries": 0,
        "max_confidence_seen": 4.6,
        "top_gate_failures": [
            {"reason": "confidence", "count": 812},
            {"reason": "reward/risk", "count": 240},
        ],
    }
    notes = zero_trade_notes(diag, scans=5689, settings=settings)
    assert "62 watch-grade setups" in notes
    assert "confidence (812x)" in notes
    assert "4.6" in notes and f"{settings.gates.min_confidence:.1f}" in notes
    assert "abstention" in notes
    assert "VIGIL_GATES__MIN_CONFIDENCE" in notes  # the conscious knob


def test_no_scans_points_at_data_coverage():
    notes = zero_trade_notes(
        {"snapshot_failures": 900, "top_gate_failures": [], "max_confidence_seen": 0.0},
        scans=0,
        settings=get_settings(),
    )
    assert "Data Health" in notes
    assert "900" in notes
