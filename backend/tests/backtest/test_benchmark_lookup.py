"""Regression: duplicate benchmark index rows must not crash the backtester.

Editing universe.yml adds instruments but never deletes old ones, so a user
who swaps ^SPX for SPY has two US index rows — one with no price bars."""

from __future__ import annotations

from datetime import date

from vigil.backtest.engine import _benchmark_series
from vigil.models import Instrument, PriceBar


def test_duplicate_index_rows_pick_the_one_with_data(session):
    stale = Instrument(
        ticker="^SPX", exchange="INDEX", market="US", name="S&P 500",
        sector="", industry="", currency="USD", security_type="index",
    )
    live = Instrument(
        ticker="SPY", exchange="NYSE", market="US", name="S&P 500 ETF",
        sector="", industry="", currency="USD", security_type="index",
    )
    session.add_all([stale, live])
    session.flush()
    for i in range(5):
        session.add(PriceBar(
            instrument_id=live.id, bar_date=date(2026, 8, 17 + i),
            open=640.0, high=645.0, low=638.0, close=642.0 + i,
            volume=1e6, currency="USD",
        ))
    session.flush()

    series = _benchmark_series(session, "US", date(2026, 8, 25))
    assert series is not None and len(series) == 5
    assert _benchmark_series(session, "UK", date(2026, 8, 25)) is None
