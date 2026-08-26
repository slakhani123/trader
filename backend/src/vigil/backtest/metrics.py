"""Backtest metric aggregation. Pure functions over simulated trades."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from vigil.indicators.stats import brier_score, reliability_curve, wilson_interval


@dataclass
class SimTrade:
    instrument_id: int
    ticker: str
    sector: str
    family: str
    horizon: str
    signal_date: date
    entry_date: date | None = None
    entry_price: float | None = None  # adjusted, cost-inclusive
    exit_date: date | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    return_pct: float | None = None
    benchmark_return_pct: float | None = None
    mae_pct: float | None = None
    mfe_pct: float | None = None
    costs_bps: float | None = None
    holding_days: int | None = None
    opportunity: float | None = None
    confidence: float | None = None
    risk: float | None = None
    regime: str = "unknown"
    market_cap_base: float | None = None
    details: dict = field(default_factory=dict)

    @property
    def closed(self) -> bool:
        return self.exit_date is not None and self.return_pct is not None

    @property
    def alpha_pct(self) -> float | None:
        if self.return_pct is None or self.benchmark_return_pct is None:
            return None
        return self.return_pct - self.benchmark_return_pct

    @property
    def won(self) -> bool | None:
        """Outcome definition: beat the benchmark by more than 2%."""
        a = self.alpha_pct
        return None if a is None else a > 2.0

    def predicted_success(self) -> float | None:
        """Deterministic mapping from scores to a success 'probability' used
        ONLY for calibration measurement (documented in docs/BACKTESTING.md):
        p = (opportunity/10) × (0.5 + confidence/20), clamped to [0.05, 0.95].
        """
        if self.opportunity is None or self.confidence is None:
            return None
        p = (self.opportunity / 10.0) * (0.5 + self.confidence / 20.0)
        return float(min(0.95, max(0.05, p)))

    def cap_band(self) -> str:
        m = self.market_cap_base
        if m is None:
            return "unknown"
        if m < 1e9:
            return "small"
        if m < 1e10:
            return "mid"
        return "large"

    def score_bucket(self) -> str:
        if self.opportunity is None:
            return "unknown"
        lo = int(self.opportunity)
        return f"{lo}-{lo + 1}"


def _pct(x: float | None) -> float | None:
    return None if x is None else round(x, 3)


def summarize(trades: list[SimTrade]) -> dict:
    closed = [t for t in trades if t.closed]
    if not closed:
        return {"n": 0, "note": "no closed trades"}
    rets = np.array([t.return_pct for t in closed], dtype=float)
    alphas = np.array([a for t in closed if (a := t.alpha_pct) is not None], dtype=float)
    wins = [t.won for t in closed if t.won is not None]
    hits = sum(1 for w in wins if w)
    ci = wilson_interval(hits, len(wins)) if wins else None
    holding = [t.holding_days for t in closed if t.holding_days is not None]
    mae = [t.mae_pct for t in closed if t.mae_pct is not None]
    mfe = [t.mfe_pct for t in closed if t.mfe_pct is not None]
    out = {
        "n": len(closed),
        "open_at_end": sum(1 for t in trades if not t.closed),
        "hit_rate": round(hits / len(wins), 3) if wins else None,
        "hit_rate_ci95": [round(ci[0], 3), round(ci[1], 3)] if ci else None,
        "avg_return_pct": _pct(float(rets.mean())),
        "median_return_pct": _pct(float(np.median(rets))),
        "avg_alpha_pct": _pct(float(alphas.mean())) if alphas.size else None,
        "win_loss_ratio": _wl(rets),
        "volatility_of_returns_pct": _pct(float(rets.std(ddof=0))),
        "avg_holding_days": round(float(np.mean(holding)), 1) if holding else None,
        "avg_mae_pct": _pct(float(np.mean(mae))) if mae else None,
        "avg_mfe_pct": _pct(float(np.mean(mfe))) if mfe else None,
        "exit_reasons": _counts([t.exit_reason for t in closed]),
    }
    return out


def _wl(rets: np.ndarray) -> float | None:
    wins = rets[rets > 0]
    losses = rets[rets < 0]
    if wins.size == 0 or losses.size == 0:
        return None
    denom = abs(float(losses.mean()))
    return round(float(wins.mean()) / denom, 2) if denom else None


def _counts(values: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def bucketize(trades: list[SimTrade]) -> dict[str, dict[str, dict]]:
    """Per-bucket summaries by strategy, horizon, sector, regime, cap band,
    score bucket. Buckets with n<20 are labelled inconclusive."""
    dims: dict[str, dict[str, list[SimTrade]]] = {
        "family": {}, "horizon": {}, "sector": {}, "regime": {},
        "cap_band": {}, "score_bucket": {},
    }
    for t in trades:
        if not t.closed:
            continue
        dims["family"].setdefault(t.family, []).append(t)
        dims["horizon"].setdefault(t.horizon, []).append(t)
        dims["sector"].setdefault(t.sector or "unknown", []).append(t)
        dims["regime"].setdefault(t.regime, []).append(t)
        dims["cap_band"].setdefault(t.cap_band(), []).append(t)
        dims["score_bucket"].setdefault(t.score_bucket(), []).append(t)
    out: dict[str, dict[str, dict]] = {}
    for dim, groups in dims.items():
        out[dim] = {}
        for key, group in sorted(groups.items()):
            s = summarize(group)
            if s.get("n", 0) < 20:
                s["inconclusive"] = True
            out[dim][key] = s
    return out


def calibration(trades: list[SimTrade]) -> dict:
    pairs = [
        (p, 1 if t.won else 0)
        for t in trades
        if t.closed and t.won is not None and (p := t.predicted_success()) is not None
    ]
    if not pairs:
        return {"n": 0}
    probs = [p for p, _ in pairs]
    outs = [o for _, o in pairs]
    return {
        "n": len(pairs),
        "brier_score": round(brier_score(probs, outs) or 0.0, 4),
        "base_rate": round(float(np.mean(outs)), 3),
        "reliability": reliability_curve(probs, outs, bins=8),
        "definition": "success = trade alpha vs matched benchmark > +2%; "
        "p = (opportunity/10)*(0.5+confidence/20)",
    }


def equity_curve_metrics(
    trades: list[SimTrade], daily_returns: pd.Series
) -> dict:
    """Portfolio-level metrics from the simulated equal-weight daily curve."""
    if daily_returns.empty:
        return {}
    curve = (1 + daily_returns).cumprod()
    total = float(curve.iloc[-1] - 1) * 100
    years = max(len(daily_returns) / 252, 1e-9)
    cagr = ((float(curve.iloc[-1])) ** (1 / years) - 1) * 100 if curve.iloc[-1] > 0 else None
    vol = float(daily_returns.std(ddof=0)) * math.sqrt(252) * 100
    sharpe = (
        float(daily_returns.mean() / daily_returns.std(ddof=0) * math.sqrt(252))
        if daily_returns.std(ddof=0) > 0
        else None
    )
    downside = daily_returns[daily_returns < 0]
    sortino = (
        float(daily_returns.mean() / downside.std(ddof=0) * math.sqrt(252))
        if len(downside) > 3 and downside.std(ddof=0) > 0
        else None
    )
    running_max = curve.cummax()
    max_dd = float((curve / running_max - 1).min()) * 100
    n_days = len(daily_returns)
    closed = [t for t in trades if t.closed]
    turnover = len(closed) / max(years, 1e-9)
    return {
        "total_return_pct": round(total, 2),
        "cagr_pct": round(cagr, 2) if cagr is not None else None,
        "annualised_vol_pct": round(vol, 2),
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "sortino": round(sortino, 2) if sortino is not None else None,
        "max_drawdown_pct": round(max_dd, 2),
        "trades_per_year": round(turnover, 1),
        "trading_days": n_days,
    }
