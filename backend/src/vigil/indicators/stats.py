"""Statistical helpers shared by engines and scoring. Pure functions."""

from __future__ import annotations

import itertools
import math

import numpy as np
import pandas as pd


def clamp(value: float, low: float = 0.0, high: float = 10.0) -> float:
    return max(low, min(high, value))


def scale_linear(
    value: float, worst: float, best: float, low: float = 0.0, high: float = 10.0
) -> float:
    """Map value from [worst, best] onto [low, high], clamped. Works with
    worst > best (inverted scales) too."""
    if worst == best:
        return (low + high) / 2
    frac = (value - worst) / (best - worst)
    return clamp(low + frac * (high - low), low, high)


def percentile_of(value: float, population: list[float] | np.ndarray) -> float | None:
    """Percentile rank (0-100) of value within population."""
    arr = np.asarray([p for p in population if p is not None and not math.isnan(p)], dtype=float)
    if arr.size < 3:
        return None
    return float((arr <= value).mean() * 100.0)


def zscore_of(value: float, population: list[float] | np.ndarray) -> float | None:
    arr = np.asarray([p for p in population if p is not None and not math.isnan(p)], dtype=float)
    if arr.size < 3:
        return None
    sd = float(arr.std(ddof=0))
    if sd == 0:
        return None
    return float((value - arr.mean()) / sd)


def cagr(first: float, last: float, years: float) -> float | None:
    if first <= 0 or last <= 0 or years <= 0:
        return None
    return float((last / first) ** (1.0 / years) - 1.0)


def beta(returns: pd.Series, benchmark_returns: pd.Series, min_obs: int = 60) -> float | None:
    joined = pd.concat([returns, benchmark_returns], axis=1, join="inner").dropna()
    if len(joined) < min_obs:
        return None
    a = joined.iloc[:, 0].to_numpy(dtype=float)
    b = joined.iloc[:, 1].to_numpy(dtype=float)
    var = float(np.var(b))
    if var == 0:
        return None
    return float(np.cov(a, b)[0, 1] / var)


def downside_beta(
    returns: pd.Series, benchmark_returns: pd.Series, min_obs: int = 30
) -> float | None:
    """Beta measured only on days the benchmark fell."""
    joined = pd.concat([returns, benchmark_returns], axis=1, join="inner").dropna()
    down = joined[joined.iloc[:, 1] < 0]
    if len(down) < min_obs:
        return None
    a = down.iloc[:, 0].to_numpy(dtype=float)
    b = down.iloc[:, 1].to_numpy(dtype=float)
    var = float(np.var(b))
    if var == 0:
        return None
    return float(np.cov(a, b)[0, 1] / var)


def correlation(a: pd.Series, b: pd.Series, min_obs: int = 60) -> float | None:
    joined = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(joined) < min_obs:
        return None
    return float(joined.iloc[:, 0].corr(joined.iloc[:, 1]))


def trend_consistency(values: list[float]) -> float | None:
    """Share of consecutive changes that move in the same direction as the
    overall change. 1.0 = perfectly persistent growth/decline."""
    vals = [v for v in values if v is not None]
    if len(vals) < 3:
        return None
    overall = vals[-1] - vals[0]
    if overall == 0:
        return 0.5
    sign = 1.0 if overall > 0 else -1.0
    steps = [(b - a) * sign > 0 for a, b in itertools.pairwise(vals)]
    return float(sum(steps) / len(steps))


def sharpe(returns: pd.Series, risk_free_daily: float = 0.0, annualise: int = 252) -> float | None:
    r = returns.dropna() - risk_free_daily
    if len(r) < 20 or float(r.std(ddof=0)) == 0:
        return None
    return float(r.mean() / r.std(ddof=0) * math.sqrt(annualise))


def sortino(returns: pd.Series, risk_free_daily: float = 0.0, annualise: int = 252) -> float | None:
    r = returns.dropna() - risk_free_daily
    downside = r[r < 0]
    if len(r) < 20 or len(downside) < 5:
        return None
    dd = float(downside.std(ddof=0))
    if dd == 0:
        return None
    return float(r.mean() / dd * math.sqrt(annualise))


def brier_score(probabilities: list[float], outcomes: list[int]) -> float | None:
    if not probabilities or len(probabilities) != len(outcomes):
        return None
    p = np.asarray(probabilities, dtype=float)
    o = np.asarray(outcomes, dtype=float)
    return float(np.mean((p - o) ** 2))


def reliability_curve(
    probabilities: list[float], outcomes: list[int], bins: int = 10
) -> list[dict]:
    """Reliability (calibration) bins: predicted vs observed frequency."""
    if not probabilities:
        return []
    p = np.asarray(probabilities, dtype=float)
    o = np.asarray(outcomes, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    rows: list[dict] = []
    for i in range(bins):
        mask = (p >= edges[i]) & (p < edges[i + 1] if i < bins - 1 else p <= edges[i + 1])
        if mask.sum() == 0:
            continue
        rows.append(
            {
                "bin_low": float(edges[i]),
                "bin_high": float(edges[i + 1]),
                "predicted": float(p[mask].mean()),
                "observed": float(o[mask].mean()),
                "count": int(mask.sum()),
            }
        )
    return rows


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for a proportion — used for hit-rate uncertainty."""
    if n == 0:
        return None
    phat = successes / n
    denom = 1 + z**2 / n
    centre = (phat + z**2 / (2 * n)) / denom
    margin = z * math.sqrt(phat * (1 - phat) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))
