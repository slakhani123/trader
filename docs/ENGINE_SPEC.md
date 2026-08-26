# Engine specification

The binding contract for the seven research engines. Downstream code
(composite scoring, signal rules, alert builder) consumes exactly what this
document promises, so deviations break the pipeline.

## Common rules (all engines)

- Signature: `analyse(snapshot: InstrumentSnapshot, settings: Settings) -> EngineResult`
  in `vigil/engines/<name>.py`. Pure function: no I/O, no clock, no
  randomness — identical snapshot ⇒ identical result.
- Read the types in `vigil/schemas/core.py` and helpers in
  `vigil/engines/base.py` (`ev`, `derived_ref`, `price_ref`,
  `sector_class`, `abstain`). Indicators live in `vigil/indicators/ta.py`
  and `vigil/indicators/stats.py` — extend those modules rather than
  duplicating maths inside an engine.
- **Score semantics**: `score` is 0.0–10.0 where 10 = maximally attractive
  *on this engine's dimension alone*, 5 ≈ neutral. `score=None` means the
  engine abstains (insufficient/inappropriate data) — abstain rather than
  guess, and say why in `warnings`.
- **Evidence**: every number that influenced the score materially appears as
  an `Evidence` item with a real `SourceRef` (fundamentals/news/estimates
  carry their record's source; computed metrics use `derived_ref` with the
  formula name and the underlying record's timestamps). Statements are
  deterministic f-string templates, human-readable, and include the value
  and period, e.g. `"ROIC (TTM) is 18.4%"`. Direction: `supports` if it
  argues for attractiveness on this engine's dimension, `contradicts` if it
  argues against, else `neutral`. 4–12 evidence items is the target range.
- **data_quality**: 0–1, how complete/fresh the inputs this engine needed
  were (not the score's strength).
- **warnings**: data problems, sector-inappropriateness, abstention reasons.
- **components**: named sub-scores (0–10) as specified per engine below.
- **details**: machine-readable extras promised below; keys are part of the
  contract.
- Handle missing fields (`None`) everywhere; never raise on absent data.
- UK names are quoted in GBP units (see ASSUMPTIONS.md), currency handling
  is otherwise irrelevant inside engines (ratios are currency-neutral).

## 1. quality  (`vigil/engines/quality.py`)

Sector-aware fundamental quality. Use `sector_class(snapshot)`:

- `general`: growth (revenue/EPS/FCF/book CAGR + persistence via
  `stats.trend_consistency` + acceleration), margins (gross/operating/FCF,
  level + trend), returns (ROIC = NOPAT/(equity+debt−cash), ROE,
  incremental ROIC), cash quality (OCF/NI, accruals = (NI−OCF)/assets,
  receivables growing faster than revenue), balance sheet (net debt/EBITDA,
  interest coverage, current ratio, debt due within 1y vs cash =
  refinancing risk), shareholder alignment (dilution from share count,
  SBC/revenue, buybacks+dividend sustainability = payout vs FCF), insider
  ownership signals from `snapshot.insiders` (cluster buys support, heavy
  selling contradicts), concentration (`largest_customer_pct`).
- `bank`: use `sector_metrics` (net_interest_margin, cet1_ratio,
  loan_loss_provisions, tangible_book_per_share) + ROE + operating margin;
  do NOT use FCF/capex/EV metrics; leverage rules don't apply — capital
  adequacy replaces them.
- `insurer`: like bank but tolerate missing bank metrics; rely on ROE,
  book-value growth.
- `reit`: use FFO (sector_metrics), occupancy, LTV; dividends vs FFO for
  sustainability; do not punish "high debt" beyond LTV bands.
- `commodity`: score cyclically — normalise margins over the full history
  (mid-cycle), flag peak-margin risk instead of extrapolating.
- `early_stage`: growth + runway (cash / FCF burn); cap score at 6 and add
  a warning — quality is unprovable pre-profit.

Accounting red flags (each adds to `details.red_flags` and contradicting
evidence): recent restatement (`is_restatement` within ~18 months), auditor
change between consecutive reports, receivables growth outpacing revenue
by >1.5x over 4 quarters, persistent `adjusted_profit_exclusions` (>20% of
|NI| in ≥3 of last 6 quarters), OCF/NI < 0.7 sustained (4-quarter avg),
inventory growth outpacing revenue similarly.

**components**: `quality`, `growth`, `balance_sheet`, `cash_quality`,
`shareholder` (each 0–10). Engine `score` = weighted blend (document
weights in module docstring).
**details**: `sector_class`, `red_flags: list[str]`,
`value_trap_inputs: {structural_revenue_decline: bool, margin_collapse:
bool, excess_leverage: bool, dilution: bool, weak_cash_conversion: bool,
governance_flags: bool}`, `growth_metrics: {revenue_cagr_3y, eps_cagr_3y,
fcf_cagr_3y (nullable floats)}`, `net_debt_ebitda`, `interest_coverage`,
`refinancing_risk: bool`, `dilution_pct_1y`.
**Abstain** when fewer than 4 visible quarterly reports.

## 2. valuation  (`vigil/engines/valuation.py`)

Multi-anchor point-in-time valuation:

- Compute (nullable): earnings yield (TTM E/P), FCF yield, EV/EBIT(DA→use
  operating income as EBITDA proxy is NOT ok — use EV/EBIT and label it),
  EV/sales, P/B, PEG (PE / forward EPS growth from estimates), dividend
  yield. For `reit`: P/FFO and dividend yield replace PE/FCF; for `bank`:
  P/TBV and PE.
- **Own history**: percentile of today's primary multiple vs its own
  point-in-time history — reconstruct a quarterly series of the multiple
  using prices and *then-visible* TTM fundamentals from
  `snapshot.fundamentals` (the snapshot is already PIT-filtered; walking
  backwards inside it is allowed since each record carries published_at —
  only use records with published_at ≤ each historical evaluation date).
- **Peers**: compare vs `snapshot.peers[*].metrics` (pe_ttm, ev_ebit,
  ev_sales, fcf_yield, pb, gross_margin, revenue_growth_ttm) —
  `stats.percentile_of` / `zscore_of`; quality-adjust: note when a discount
  is explained by slower growth/lower margin than peers.
- **Scenarios** (`details.scenarios`): conservative deterministic anchors —
  base = mid of historical-median multiple × current TTM metric and a
  reverse-DCF-lite fair value (document formula); bear = trough multiple ×
  stressed metric (−1σ growth); bull = 75th-pct multiple × modestly grown
  metric. Each scenario: `{price, rationale}` in LOCAL currency. Prices
  rounded to 2dp; never claim precision.
- **Analyst targets**: age-weight (`median_age_days`), report count,
  dispersion, implied upside, 30-day drift — as *evidence only*, never in
  fair value. Low count (<5) or high dispersion (std/mean > 0.25) adds a
  warning and contradicting evidence when leaned on.
- **Value-trap test** (`details.value_trap`): combine `quality` engine
  inputs recomputed locally (do not import the quality engine): structural
  revenue decline (TTM rev < TTM rev 2y ago AND negative 3y CAGR), margin
  collapse (op margin down >40% vs 3y avg), excess leverage (net
  debt/EBIT > 5 or refinancing wall), dilution (>3%/y), weak cash conversion
  (OCF/NI 4q avg < 0.6), estimate revisions negative (net down over 90d),
  no catalyst within horizon, cyclically-peak earnings (margin > 1.5× its
  own 5y median). Emit `{is_trap_risk: bool, failed_checks: list[str]}`.
  **A cheap multiple with ≥2 failed checks caps score at 4.**
**components**: `absolute` (yield-based attractiveness), `vs_history`,
`vs_peers`, `scenario_asymmetry` (upside/downside ratio from scenarios).
**details**: `scenarios: {base: {price, rationale}, bull: …, bear: …}`,
`fair_value_low`, `fair_value_high` (base±), `primary_multiple: str`,
`multiples: dict`, `value_trap`, `target_summary: {mean, implied_upside_pct,
count, dispersion_pct, median_age_days} | None`, `entry_zone_hint:
{low, high} | None` (a value-anchored accumulation band ≤ current price
when attractive, else None).
**Abstain** when <4 quarters visible or market cap unknown.

## 3. technical  (`vigil/engines/technical.py`)

Multi-timeframe price/volume evidence using `indicators/ta.py`
(sma/slopes 20/50/100/200, RSI, MACD, Bollinger, ATR, realised vol,
volume trend, relative strength vs `snapshot.benchmark` and
`snapshot.sector_index` at 1/3/6/12m, `momentum_12_1`, breakout state,
higher-highs/lows, anchored VWAP from the latest guidance/earnings event
date in `snapshot.catalysts` if any (else 6m ago), `support_zones`,
`resistance_levels`, `gap_analysis`, drawdown from 52w high, max drawdown).

Score = trend structure (30%) + setup quality (40%) + confirmation (30%):
- Trend: price vs stacked MAs, MA slopes, HH/HL structure.
- Setup: proximity to statistically tested support with stabilisation
  (tight recent range or RSI divergence) OR confirmed breakout from
  consolidation; **an oversold RSI alone must add ≤1 point**; reward/risk
  = (nearest resistance − price) / (price − support zone mid), capped 5.
- Confirmation: volume vs 3m average on the setup, RS improving.
Penalties: parabolic extension (price > 30% above sma50 or > 3× ATR above
sma20), large unfilled up-gaps beneath price, breakdown below the 200d MA
on volume, failed breakout within 10 bars.
**components**: `trend`, `setup`, `confirmation`.
**details**: `support_zones` (from ta, list of {low, high, strength,
basis}), `resistance_levels: list[float]`, `nearest_support:
{low, high} | None`, `stop_hint: float | None` (zone low − 1×ATR),
`reward_risk: float | None`, `entry_zone_hint: {low, high} | None`,
`breakout: dict` (ta.breakout_state), `trend_state:
'uptrend'|'downtrend'|'range'`, `extended: bool`, `atr_pct`,
`realised_vol_annual`, `drawdown_from_52w_high_pct`, `rsi14`,
`above_sma200: bool`, `anchored_vwap: float | None`,
`rs_3m_market: float | None`.
**Abstain** with <120 bars. Add warning when volume data is degenerate.

## 4. momentum  (`vigil/engines/momentum.py`)

Cross-sectional + fundamental momentum:
- Price momentum 1/3/6/12m and 12-1, RS vs market & sector (1/3/6m),
  volume-confirmation (up-day volume vs down-day volume 3m).
- Fundamental momentum from `snapshot.estimates`: revision breadth
  ((up−down)/analysts, 30d), magnitude ((mean−mean_90d)/|mean_90d|),
  guidance events from news (`source_type='factual_event'` with 'guidance'
  in headline) — and earnings surprises from catalyst outcomes
  (`kind='earnings'` resolved, parse `outcome` "EPS surprise ±x%" pattern;
  treat unparseable outcomes as absent).
- Margin inflection: operating margin turning positive or rising ≥3pp YoY
  from fundamentals.
- Confluence bonus when price momentum, revisions and RS agree.
Penalties (each explicit contradicting evidence + entry in
`details.penalties`): parabolic (3m return > 50% and price > 30% over
sma50), crowding (short interest pct_float > 10 rising, or social share of
news > 40%), low liquidity move, unfilled gap support (>5% gap below),
binary event within 10 trading days (from catalysts), negative divergence
(price up 3m but net revisions down), fundamentals deteriorating under
price strength (TTM revenue shrinking while 6m return > 20%).
**components**: `price_momentum`, `fundamental_momentum`, `confirmation`.
**details**: `returns: {m1, m3, m6, m12, m12_1}`, `rs: {market_1m,
market_3m, market_6m, sector_3m}`, `revision_breadth_30d`,
`revision_magnitude_90d`, `surprise_last: float | None`,
`margin_inflection: bool`, `penalties: list[str]`, `parabolic: bool`,
`accumulation_breakout: bool` (breakout state == breakout with volume
ratio ≥ 1.3 after ≥60-bar consolidation).
**Abstain** with <260 bars (needs 12m).

## 5. sentiment  (`vigil/engines/sentiment.py`)

Deterministic narrative processing over `snapshot.news` (sentiment values
are provider/lexicon supplied — this engine aggregates, it does not score
text):
- Source weights: factual_event 1.0, analyst_opinion 0.7,
  management_claim 0.4, market_commentary 0.5, social 0.15.
- Weight × novelty × time decay (half-life 21 days to as_of).
- Direction = weighted mean; rate of change = last 30d mean vs prior 60d;
  disagreement = weighted std + explicit management-vs-analyst divergence;
  volume = weighted item count.
- Price confirmation: correlate sign of direction with 1m return —
  narrative confirmed/unconfirmed/contradicted.
- Contrarian flag: direction < −0.4 AND rate of change turning up AND
  (balance-sheet survivability must come from elsewhere — emit
  `contrarian_candidate: bool` and let signal rules combine it with the
  quality engine; do NOT boost the score for it here beyond +0.5).
- Social-heavy flow (>40% weight share) caps score at 6 and warns.
Score 5 = neutral; sustained, novel, confirmed positive narrative scores
high; deteriorating narrative with price breakdown scores low.
**components**: `direction`, `momentum` (rate of change), `agreement`,
`confirmation`.
**details**: `direction_score` (−1..1), `rate_of_change`, `disagreement`,
`price_confirms: 'confirmed'|'unconfirmed'|'contradicted'`,
`share_by_type: dict`, `contrarian_candidate: bool`, `item_count`,
`weighted_volume`.
**Abstain** when snapshot has no news at all (score None, data_quality 0).

## 6. catalyst  (`vigil/engines/catalyst.py`)

Forward event analysis over `snapshot.catalysts`:
- For each unresolved catalyst: days until expected_date, kind relevance
  weight (earnings .8, guidance .9, regulatory .9, refinancing 1.0 when
  leverage matters, m_and_a 1.0, capital_return .7, contract .6,
  product_launch .5, investor_day .4, index_change .5, management_change
  .5, filing .3), date confidence (`date_confirmed`), binariness.
- Priced-in heuristic (documented, deterministic): run-up = 20-day return
  minus benchmark; a positive catalyst with >10% excess run-up is
  partially priced in — scale its contribution down linearly (fully
  priced-in at +25%). NEVER output invented probabilities; binariness is a
  flag from data, priced-in is a labelled heuristic.
- Score: density and quality of *near, relevant, credible* catalysts
  within each horizon window (use settings.horizons); resolved recent
  catalysts with positive outcomes add a small tail-wind component.
- Binary events near-term REDUCE the score's contribution to buy-setups —
  emit `binary_event_within: {days: int, kind} | None` so confidence
  gating can act on it; the engine itself scores the *opportunity* of the
  catalyst calendar, listing binary proximity as contradicting evidence.
**components**: `near_term` (≤1m), `medium_term` (≤6m), `long_term`,
`recent_outcomes`.
**details**: `upcoming: list[{kind, date (iso), days, binary, confirmed,
description, priced_in_pct (0-100), relevance}]`,
`binary_event_within_20d: bool`, `next_binary: {kind, date, days} | None`,
`next_earnings: {date, days, confirmed} | None`.
Score 5 when calendar is empty but data exists; abstain only when the
snapshot carries no catalyst data at all AND no news (cannot distinguish
"no events" from "no coverage" — use quality.missing to decide:
if 'news' in snapshot.quality.missing → abstain).

## 7. regime  (`vigil/engines/regime.py`)

Environment classification + instrument risk. Two jobs:

**(a) Regime classification** from `snapshot.benchmark`, `snapshot.macro`
(vix, rates, cpi, credit spreads), breadth proxy (sector index vs market):
label `bull | correction | bear | stress | recovery | choppy` using
documented deterministic rules (e.g. benchmark vs its 200d MA and slope,
drawdown from high, vix level/trend, credit spread level/trend). Emit
`regime_adjustment` ∈ [−0.75, +0.25] — a MODEST, documented score tilt
applied by composite scoring to short/medium horizons only.

**(b) Instrument risk profile**: beta & downside beta vs benchmark
(2y daily), realised vol (1y), max drawdown (2y), liquidity risk (traded
value vs universe minimum, spread estimate), gap risk (frequency of >5%
daily moves), leverage amplification (from fundamentals: net debt/EBIT
bands; banks/REITs use their own leverage semantics), binary-event
proximity (catalysts), rate sensitivity (REIT/high-leverage + rising
rates), FX exposure (currency ≠ GBP base), momentum-crash vulnerability
(high 12m return + high short interest + regime != bull).

Engine `score` = environment favourability for THIS instrument (10 =
benign regime and low instrument risk). Also emit `details.risk_score`
0–10 (10 = extremely risky) — the composite Risk Score starts from this.
**components**: `regime` (market environment 0-10), `instrument_risk`
(inverted: 10 = low risk), `liquidity_risk` (10 = liquid).
**details**: `regime_label`, `regime_adjustment`, `risk_score` (0-10),
`risk_factors: list[str]` (each a short reason), `beta`, `downside_beta`,
`realised_vol_1y`, `max_drawdown_2y`, `gap_risk_freq` (share of days with
|move|>5% in 1y), `liquidity_band: 'high'|'medium'|'low'|'very_low'`,
`binary_event_risk: bool`, `momentum_crash_risk: bool`.
Abstain only when benchmark series is empty.

## Testing expectations (each engine)

`tests/engines/test_<name>.py` using `tests/factories.py`:
- happy-path: archetype snapshot produces high/low score in the right
  direction, evidence non-empty, every evidence value non-None has a
  SourceRef with provider set;
- abstention: minimal snapshot abstains with a warning;
- determinism: same snapshot twice ⇒ identical `model_dump()`;
- at least one sector-aware behaviour (quality/valuation) or
  penalty/edge-case behaviour (others);
- details contract: promised keys present with correct types.
Run: `cd backend && .venv/bin/python -m pytest tests/engines/test_<name>.py -q`
Lint: `.venv/bin/ruff check src/vigil/engines/<name>.py tests/engines/test_<name>.py`.
