"""Technical indicators. Pure functions over pandas Series/DataFrames.

Conventions:
* Input price frames are daily, indexed by ascending DatetimeIndex, with
  columns open/high/low/close/adj_close/volume. Functions operate on the
  ADJUSTED close unless they explicitly need raw OHLC ranges.
* Every function returns NaN-padded Series aligned to the input index, or a
  scalar (float | None). None/NaN means "not computable" — never a guess.
* No look-ahead: everything uses only values at or before each index point.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def slope(series: pd.Series, window: int = 20) -> float | None:
    """Normalised slope of the last ``window`` values: per-day change as a
    fraction of the mean level (comparable across price scales)."""
    tail = series.dropna().iloc[-window:]
    if len(tail) < window:
        return None
    y = tail.to_numpy(dtype=float)
    x = np.arange(len(y), dtype=float)
    denom = float(np.mean(y))
    if denom == 0:
        return None
    coef = np.polyfit(x, y, 1)[0]
    return float(coef / denom)


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(100.0).where(avg_loss.notna() | avg_gain.isna(), 100.0)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(series, fast) - ema(series, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger(series: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    mid = sma(series, window)
    std = series.rolling(window, min_periods=window).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid
    pct_b = (series - lower) / (upper - lower)
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower, "width": width, "pct_b": pct_b})


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()


def realised_volatility(series: pd.Series, window: int = 21, annualise: int = 252) -> pd.Series:
    rets = np.log(series / series.shift(1))
    return rets.rolling(window, min_periods=window).std(ddof=0) * math.sqrt(annualise)


def momentum(series: pd.Series, periods: int) -> float | None:
    """Simple return over ``periods`` trading days ending at the last bar."""
    s = series.dropna()
    if len(s) <= periods:
        return None
    start = float(s.iloc[-periods - 1])
    if start == 0:
        return None
    return float(s.iloc[-1] / start - 1.0)


def momentum_12_1(series: pd.Series) -> float | None:
    """Classic 12-month momentum excluding the most recent month."""
    s = series.dropna()
    if len(s) < 252:
        return None
    start, end = float(s.iloc[-252]), float(s.iloc[-21])
    if start == 0:
        return None
    return float(end / start - 1.0)


def relative_strength(series: pd.Series, benchmark: pd.Series, window: int) -> float | None:
    """Return differential vs a benchmark over ``window`` days (aligned)."""
    joined = pd.concat([series, benchmark], axis=1, join="inner").dropna()
    if len(joined) <= window:
        return None
    a = joined.iloc[:, 0]
    b = joined.iloc[:, 1]
    ra = float(a.iloc[-1] / a.iloc[-window - 1] - 1.0)
    rb = float(b.iloc[-1] / b.iloc[-window - 1] - 1.0)
    return ra - rb


def drawdown_from_high(series: pd.Series, lookback: int = 252) -> float | None:
    s = series.dropna().iloc[-lookback:]
    if s.empty:
        return None
    peak = float(s.max())
    if peak == 0:
        return None
    return float(s.iloc[-1] / peak - 1.0)


def max_drawdown(series: pd.Series, lookback: int | None = None) -> float | None:
    s = series.dropna()
    if lookback:
        s = s.iloc[-lookback:]
    if len(s) < 2:
        return None
    running_max = s.cummax()
    dd = s / running_max - 1.0
    return float(dd.min())


def anchored_vwap(df: pd.DataFrame, anchor: pd.Timestamp) -> float | None:
    """Volume-weighted average price from ``anchor`` (inclusive) to the end.
    Uses typical price (H+L+C)/3 on the adjusted scale when available."""
    window = df.loc[df.index >= anchor]
    if window.empty or window["volume"].sum() <= 0:
        return None
    if {"high", "low"}.issubset(window.columns):
        scale = window["adj_close"] / window["close"]
        typical = (window["high"] * scale + window["low"] * scale + window["adj_close"]) / 3.0
    else:
        typical = window["adj_close"]
    return float((typical * window["volume"]).sum() / window["volume"].sum())


def swing_levels(
    series: pd.Series, lookback: int = 252, order: int = 5
) -> tuple[list[float], list[float]]:
    """Prior swing highs and lows: local extrema with ``order`` bars on each
    side. Returns (highs, lows) sorted descending by recency."""
    s = series.dropna().iloc[-lookback:]
    vals = s.to_numpy(dtype=float)
    highs: list[float] = []
    lows: list[float] = []
    for i in range(order, len(vals) - order):
        window = vals[i - order : i + order + 1]
        if vals[i] == window.max() and (window.argmax() == order):
            highs.append(float(vals[i]))
        if vals[i] == window.min() and (window.argmin() == order):
            lows.append(float(vals[i]))
    return highs[::-1], lows[::-1]


def volume_profile_zones(
    df: pd.DataFrame, lookback: int = 252, bins: int = 24, top_n: int = 3
) -> list[tuple[float, float]]:
    """High-volume price areas: histogram of volume by adjusted-price bin;
    returns the ``top_n`` densest (low, high) price bands."""
    win = df.dropna(subset=["adj_close", "volume"]).iloc[-lookback:]
    if len(win) < bins:
        return []
    prices = win["adj_close"].to_numpy(dtype=float)
    vols = win["volume"].to_numpy(dtype=float)
    hist, edges = np.histogram(prices, bins=bins, weights=vols)
    order = np.argsort(hist)[::-1][:top_n]
    zones = [(float(edges[i]), float(edges[i + 1])) for i in sorted(order)]
    return zones


def support_zones(
    df: pd.DataFrame, lookback: int = 252
) -> list[dict]:
    """Statistically motivated support zones below the current price:
    clusters of swing lows reinforced by high-volume areas and round SMA
    levels. Returns list of {low, high, strength, basis[]} sorted by
    proximity to the last price (nearest first)."""
    if df.empty:
        return []
    close = float(df["adj_close"].iloc[-1])
    _, lows = swing_levels(df["adj_close"], lookback=lookback)
    candidates: list[tuple[float, str]] = [(lv, "swing_low") for lv in lows if lv < close]
    for zlow, zhigh in volume_profile_zones(df, lookback=lookback):
        mid = (zlow + zhigh) / 2
        if mid < close:
            candidates.append((mid, "volume_node"))
    for w in (50, 100, 200):
        m = sma(df["adj_close"], w)
        if not m.dropna().empty:
            lvl = float(m.iloc[-1])
            if lvl < close:
                candidates.append((lvl, f"sma{w}"))
    if not candidates:
        return []
    # Cluster candidates within 2% of each other.
    candidates.sort(key=lambda t: t[0])
    zones: list[dict] = []
    cur: list[tuple[float, str]] = [candidates[0]]
    for lvl, basis in candidates[1:]:
        if lvl <= cur[-1][0] * 1.02:
            cur.append((lvl, basis))
        else:
            zones.append(_zone(cur))
            cur = [(lvl, basis)]
    zones.append(_zone(cur))
    zones.sort(key=lambda z: close - z["high"])
    return zones


def _zone(members: list[tuple[float, str]]) -> dict:
    lows = [m[0] for m in members]
    return {
        "low": min(lows) * 0.995,
        "high": max(lows) * 1.005,
        "strength": len(members),
        "basis": sorted({m[1] for m in members}),
    }


def resistance_levels(df: pd.DataFrame, lookback: int = 252) -> list[float]:
    if df.empty:
        return []
    close = float(df["adj_close"].iloc[-1])
    highs, _ = swing_levels(df["adj_close"], lookback=lookback)
    return sorted([h for h in highs if h > close])[:5]


def breakout_state(df: pd.DataFrame, lookback: int = 126, confirm_volume: float = 1.3) -> dict:
    """Detects breakout / failed breakout / consolidation over ``lookback``.

    Returns dict with keys: state (breakout|failed_breakout|consolidating|
    trending|none), range_high, range_low, volume_ratio, days_since_break.
    """
    s = df["adj_close"].dropna().iloc[-lookback:]
    if len(s) < 40:
        return {"state": "none"}
    base = s.iloc[:-10]
    range_high = float(base.max())
    range_low = float(base.min())
    last = float(s.iloc[-1])
    vol = df["volume"].dropna().iloc[-lookback:]
    recent_vol = float(vol.iloc[-10:].mean()) if len(vol) >= 20 else 0.0
    base_vol = float(vol.iloc[:-10].mean()) if len(vol) >= 20 else 0.0
    volume_ratio = recent_vol / base_vol if base_vol > 0 else 1.0
    band = (range_high - range_low) / range_high if range_high else 1.0

    broke_idx = s.iloc[-10:] > range_high
    days_since = int(broke_idx[::-1].idxmax() == broke_idx.index[-1]) if broke_idx.any() else None
    if last > range_high:
        state = "breakout" if volume_ratio >= confirm_volume else "unconfirmed_breakout"
    elif broke_idx.any() and last < range_high:
        state = "failed_breakout"
    elif band < 0.12:
        state = "consolidating"
    else:
        state = "trending"
    return {
        "state": state,
        "range_high": range_high,
        "range_low": range_low,
        "volume_ratio": round(volume_ratio, 2),
        "band_width": round(band, 3),
        "days_since_break": days_since,
    }


def higher_highs_lows(series: pd.Series, lookback: int = 126) -> str:
    """'higher_highs_lows' | 'lower_highs_lows' | 'mixed' from swing structure."""
    highs, lows = swing_levels(series, lookback=lookback, order=5)
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1] > highs[-2]
        hl = lows[-1] > lows[-2]
        if hh and hl:
            return "higher_highs_lows"
        if not hh and not hl:
            return "lower_highs_lows"
    return "mixed"


def gap_analysis(df: pd.DataFrame, lookback: int = 40, threshold: float = 0.04) -> dict:
    """Recent unfilled gaps: date, direction, size, and whether price has
    since traded back through the gap (filled)."""
    win = df.iloc[-lookback:]
    gaps: list[dict] = []
    prev_close = None
    for ts, row in win.iterrows():
        if prev_close is not None and prev_close > 0:
            gap = (row["open"] - prev_close) / prev_close
            if abs(gap) >= threshold:
                after = df.loc[df.index > ts, ["low", "high"]]
                filled = bool(
                    (gap > 0 and not after.empty and float(after["low"].min()) <= prev_close)
                    or (gap < 0 and not after.empty and float(after["high"].max()) >= prev_close)
                )
                gaps.append(
                    {"date": str(ts.date()), "size_pct": round(gap * 100, 1), "filled": filled}
                )
        prev_close = row["close"]
    unfilled_up = [g for g in gaps if g["size_pct"] > 0 and not g["filled"]]
    return {"gaps": gaps, "unfilled_up_gaps": len(unfilled_up)}
