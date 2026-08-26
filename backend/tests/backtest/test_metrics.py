"""Backtest metric aggregation: summaries, buckets, calibration, CIs."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from vigil.backtest.metrics import (
    SimTrade,
    bucketize,
    calibration,
    equity_curve_metrics,
    summarize,
)


def trade(i: int, ret: float, bench: float = 0.0, family: str = "deep_value",
          horizon: str = "medium", opportunity: float = 7.0, confidence: float = 6.0,
          regime: str = "bull", mcap: float = 5e9) -> SimTrade:
    entry = date(2025, 1, 6) + timedelta(days=7 * i)
    exit_ = entry + timedelta(days=30)
    return SimTrade(
        instrument_id=i, ticker=f"T{i}", sector="Technology", family=family,
        horizon=horizon, signal_date=entry - timedelta(days=1),
        entry_date=entry, entry_price=100.0, exit_date=exit_,
        exit_price=100.0 * (1 + ret / 100), exit_reason="trim",
        return_pct=ret, benchmark_return_pct=bench, mae_pct=min(0.0, ret) - 1,
        mfe_pct=max(0.0, ret) + 1, costs_bps=20.0, holding_days=21,
        opportunity=opportunity, confidence=confidence, risk=4.0,
        regime=regime, market_cap_base=mcap,
    )


class TestSummaries:
    def test_hit_rate_and_ci(self):
        trades = [trade(i, ret=10.0, bench=2.0) for i in range(8)] + [
            trade(10 + i, ret=-5.0, bench=2.0) for i in range(2)
        ]
        s = summarize(trades)
        assert s["n"] == 10
        assert s["hit_rate"] == 0.8  # alpha > 2% for the winners only
        lo, hi = s["hit_rate_ci95"]
        assert lo < 0.8 < hi
        assert s["avg_alpha_pct"] == round((8 * 8.0 + 2 * -7.0) / 10, 3)

    def test_empty(self):
        assert summarize([])["n"] == 0

    def test_open_trades_excluded_from_stats(self):
        open_trade = SimTrade(
            instrument_id=1, ticker="X", sector="S", family="deep_value",
            horizon="long", signal_date=date(2025, 1, 2),
        )
        s = summarize([open_trade, trade(2, 5.0)])
        assert s["n"] == 1 and s["open_at_end"] == 1


class TestBuckets:
    def test_small_buckets_marked_inconclusive(self):
        trades = [trade(i, 5.0) for i in range(5)]
        buckets = bucketize(trades)
        assert buckets["family"]["deep_value"]["inconclusive"] is True
        assert buckets["family"]["deep_value"]["n"] == 5

    def test_dimensions_present(self):
        trades = [trade(i, 5.0) for i in range(3)]
        buckets = bucketize(trades)
        for dim in ("family", "horizon", "sector", "regime", "cap_band", "score_bucket"):
            assert buckets.get(dim)

    def test_cap_bands(self):
        assert trade(1, 1.0, mcap=5e8).cap_band() == "small"
        assert trade(1, 1.0, mcap=5e9).cap_band() == "mid"
        assert trade(1, 1.0, mcap=5e10).cap_band() == "large"


class TestCalibration:
    def test_brier_and_reliability(self):
        # High-score trades that win, low-score trades that lose => calibrated-ish.
        trades = [trade(i, 12.0, opportunity=8.5, confidence=8.0) for i in range(15)]
        trades += [trade(100 + i, -6.0, opportunity=5.0, confidence=4.0) for i in range(15)]
        c = calibration(trades)
        assert c["n"] == 30
        assert 0.0 <= c["brier_score"] <= 1.0
        assert c["reliability"], "reliability bins should exist"
        assert "definition" in c

    def test_probability_mapping_bounds(self):
        t = trade(1, 5.0, opportunity=10.0, confidence=10.0)
        assert t.predicted_success() == 0.95
        t2 = trade(2, 5.0, opportunity=0.5, confidence=0.5)
        assert t2.predicted_success() == 0.05


class TestEquityCurve:
    def test_metrics_from_daily_returns(self):
        idx = pd.bdate_range("2025-01-02", periods=252)
        daily = pd.Series([0.001] * 252, index=idx)
        m = equity_curve_metrics([trade(1, 5.0)], daily)
        assert m["total_return_pct"] > 20
        assert m["max_drawdown_pct"] == 0.0
        assert m["sharpe"] is None or m["sharpe"] > 0  # zero-vol edge case

    def test_empty_curve(self):
        assert equity_curve_metrics([], pd.Series(dtype=float)) == {}
