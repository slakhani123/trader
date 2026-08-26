# Formulas and scoring methodology

Everything here is deterministic and versioned. Engine modules
(`backend/src/vigil/engines/*.py`) document their own sub-formulas in their
docstrings; this file covers the shared layers. Weight tables live in
`scoring/weights.py` and are persisted per version in `model_versions`.

## Indicators (`indicators/ta.py`)

- SMA/EMA: standard, `min_periods = window` (no partial-window values).
- Slope: OLS slope of the last N values divided by their mean (per-day
  fractional change, scale-free).
- RSI(14): Wilder smoothing via `ewm(alpha=1/14)`.
- MACD(12,26,9), Bollinger(20, 2σ, population std), ATR(14, Wilder).
- Realised vol: std of daily log returns × √252.
- Momentum m-N: close[t]/close[t−N] − 1; 12-1 momentum excludes the last
  21 sessions.
- Relative strength: return differential vs benchmark over the window on
  inner-joined dates.
- Anchored VWAP: Σ(typical price × volume)/Σ(volume) from the anchor date,
  typical price = (H+L+C)/3 on the adjusted scale.
- Swing levels: local extrema with 5 bars on each side over 252 sessions.
- Support zones: swing lows + volume-profile nodes (24-bin volume-weighted
  price histogram, top 3) + 50/100/200-day SMA levels below price,
  clustered at 2% tolerance; zone strength = member count.
- Breakout state: last close vs the pre-period (excluding final 10 bars)
  range high; volume confirmation = last-10-day avg volume ≥ 1.3× base.
- Gap analysis: |open/prev close − 1| ≥ 4%, filled if price traded back
  through the gap origin.

## Point-in-time adjustment (`data/snapshot.py`)

Bars are stored raw. At snapshot time, only corporate actions with
`ex_date ≤ as_of` apply: splits divide earlier closes by the cumulative
factor (volume multiplied); dividends scale earlier adjusted closes by
`1 − amount/last_raw_close` (standard proportional method). A later action
never rewrites what an earlier snapshot saw.

## Liquidity and spread heuristic

Median daily traded value = median(close × volume) over 63 sessions.
Spread estimate: >£20m → 5bp; >£5m → 12bp; >£1m → 25bp; else 60bp.
Used for backtest costs and liquidity risk banding, documented as a
heuristic, not observed spreads.

## Composite scoring (`scoring/composite.py`)

Component scores (0–10) map from engines: quality → quality/growth/
balance_sheet sub-components; valuation/technical/momentum/sentiment/
catalyst → their own scores. Data-quality component = 10 × (0.5 ×
snapshot completeness + 0.5 × mean engine data_quality).

**Opportunity(h)** = Σ w(h,c) × component(c), renormalised over available
components, then a regime tilt bounded to [−0.75, +0.25], applied ×1.0 to
short, ×0.5 to medium, ×0 to long. Every contribution is emitted as an
explanation line stored with the score.

**Confidence(h)** starts at 10 × (0.35 + 0.65 × data quality), then
documented penalties: −0.7 per abstaining engine; up to −2 for component
disagreement (std > 1.8); −1 when >35% of evidence contradicts; −1 no
estimates / −0.7 sparse coverage (<5 analysts); −1.5/−0.7/−0.3
(short/medium/long) when a binary event is within 20 trading days; −1 low
liquidity, −2 very low; −1 when one component carries >45% of the blend;
−1.5 stale prices; −0.5 flat penalty until the model has out-of-sample
calibration. Clamped to [0,10].

**Risk(h)** starts from the regime engine's instrument risk score, then:
+1 binary event ≤20 trading days (short/medium); +1 refinancing risk;
+0.7 for ≥2 accounting red flags; +0.8 value-trap characteristics;
+0.7 technically extended (short only); +0.6 social-speculation-driven
flow. Clamped to [0,10].

**Best-fit horizon**: only when one horizon passes its gate with
confidence ≥ 5 and leads the runner-up by ≥ 0.75 opportunity; otherwise
none (deliberately — conflicting horizon conclusions are never averaged).

## Gates (`scoring/gates.py`)

Universe: security type, shell exclusion, industry exclusions, active
listing, min market cap (base ccy), min price (local), min median traded
value (base). Buy gate: min opportunity 6.5, min confidence 5.5, max risk
8.0, min data quality 0.6, ≥4 engines reporting, min reward/risk 2.0,
price staleness ≤ 3 trading days. All configurable.

## Signal rules (`signals/rules.py`)

Each family is a conjunction of documented conditions (every condition is
stored with a ✓/✗ in the alert's rationale). See the module for exact
thresholds. Highlights: a cheap multiple never triggers Deep Value with
≥2 failed value-trap checks; an oversold RSI alone never triggers
Oversold-at-Support (support zone + reward/risk + quality backdrop + no
imminent binary event are all required); AVOID suppresses buy candidates
from the same scan.

## Lifecycle (`signals/lifecycle.py`)

FSM in the module docstring. Alert dedup: state transitions always alert;
same-state refreshes need cooldown (default 5 trading days) AND a material
change (|Δopportunity| ≥ 0.7, |Δrisk| ≥ 1.0, or |Δprice| ≥ 5%). Stops
require two consecutive closes beyond the level ("price volatility" vs
"thesis failure" separation); fundamental invalidation (restatement,
red-flag accumulation, multi-way value-trap failure) is checked separately
from price.

## Narrative validation (`llm/narrative.py`)

Every number in an LLM-produced narrative must match a number present in
the evidence packet within 0.5% tolerance (integers 0–12 exempt as prose).
Violations reject the narrative and the deterministic template composer is
used instead. The alert records which path produced its text.

## Backtest costs (`config.BacktestCostConfig`)

5bp commission per side + half the liquidity-band spread estimate per side
+ one-day execution delay (signal on close, fill at next open). All
configurable; documented in docs/BACKTESTING.md.
