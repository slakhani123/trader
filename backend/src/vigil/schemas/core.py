"""The core contract.

Everything in Vigil hangs off the types in this module:

* ``SourceRef``            — where a fact came from, when it was published,
                             when we fetched it, and how fresh it is.
* ``Evidence``             — one sourced, dated, machine-keyed fact with a
                             direction (supports / contradicts / neutral).
* ``InstrumentSnapshot``   — the frozen point-in-time bundle engines see.
* ``EngineResult``         — what every research engine returns.

Engines are pure functions ``analyse(snapshot, settings) -> EngineResult``:
no I/O, no clock access, no randomness. Given the same snapshot they must
return byte-identical results. ``score=None`` means the engine abstains.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# Sourcing and evidence
# --------------------------------------------------------------------------

SourceType = Literal[
    "price", "fundamental", "estimate", "target", "news", "filing",
    "macro", "corporate_action", "short_interest", "insider", "derived",
]

Direction = Literal["supports", "contradicts", "neutral"]

Pillar = Literal[
    "quality", "growth", "valuation", "technical", "momentum",
    "sentiment", "catalysts", "balance_sheet", "risk", "data_quality",
]


class SourceRef(BaseModel):
    """Provenance for a single fact. Every Evidence carries one."""

    provider: str
    source_type: SourceType
    reference: str = Field(description="URL, filing accession no., or internal record id")
    published_at: datetime | None = Field(
        default=None, description="When the underlying information became public"
    )
    retrieved_at: datetime | None = None
    freshness_days: float | None = Field(
        default=None, description="as_of minus published_at, in days"
    )

    def describe(self) -> str:
        bits = [self.provider, self.reference]
        if self.published_at:
            bits.append(f"published {self.published_at:%Y-%m-%d %H:%M}Z")
        if self.freshness_days is not None:
            bits.append(f"{self.freshness_days:.0f}d old")
        return " · ".join(bits)


class Evidence(BaseModel):
    """One sourced fact.

    ``statement`` is produced by a deterministic template — never by an LLM.
    Alerts and narratives may only reference numbers that appear in
    evidence ``value``s or in the alert's own score/price fields.
    """

    key: str = Field(description="Stable machine key, e.g. 'roic_ttm'")
    statement: str
    value: float | str | None = None
    direction: Direction = "neutral"
    pillar: Pillar
    source: SourceRef
    as_of: date


# --------------------------------------------------------------------------
# Point-in-time records inside a snapshot
# --------------------------------------------------------------------------


class FundamentalRecord(BaseModel):
    """One reported fiscal period, normalised. Point-in-time: the snapshot
    builder only includes records with ``published_at <= as_of`` and applies
    restatements only once *their* publication date has passed."""

    model_config = ConfigDict(frozen=True)

    period_end: date
    period_type: Literal["Q", "A"]
    published_at: datetime
    is_restatement: bool = False
    restates_period_end: date | None = None
    currency: str = "USD"
    source: SourceRef

    # Income statement
    revenue: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    eps_diluted: float | None = None
    shares_diluted: float | None = None
    interest_expense: float | None = None
    # Cash flow
    operating_cash_flow: float | None = None
    capex: float | None = None
    dividends_paid: float | None = None
    buybacks: float | None = None
    stock_based_comp: float | None = None
    # Balance sheet
    total_assets: float | None = None
    total_equity: float | None = None
    total_debt: float | None = None
    cash_and_equivalents: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    receivables: float | None = None
    inventory: float | None = None
    goodwill_intangibles: float | None = None
    debt_due_within_1y: float | None = None
    # Disclosures
    largest_customer_pct: float | None = None
    auditor: str | None = None
    adjusted_profit_exclusions: float | None = Field(
        default=None, description="Gap between 'adjusted' and statutory profit, if disclosed"
    )
    # Sector-specific extras (banks/insurers/REITs/commodity), keyed by name,
    # e.g. {"ffo": ..., "net_interest_income": ..., "cet1_ratio": ...}
    sector_metrics: dict[str, float] = Field(default_factory=dict)

    @property
    def free_cash_flow(self) -> float | None:
        if self.operating_cash_flow is None or self.capex is None:
            return None
        return self.operating_cash_flow - abs(self.capex)


class EstimateRecord(BaseModel):
    """Consensus estimate snapshot for one metric/period as of a date."""

    as_of: date
    metric: Literal["eps", "revenue"]
    fiscal_label: str  # e.g. "FY2026" or "Q3-2026"
    period_end: date
    mean: float
    high: float | None = None
    low: float | None = None
    analyst_count: int = 0
    mean_30d_ago: float | None = None
    mean_90d_ago: float | None = None
    up_revisions_30d: int = 0
    down_revisions_30d: int = 0
    source: SourceRef


class TargetRecord(BaseModel):
    """Consensus price-target snapshot. Supporting evidence, never truth."""

    as_of: date
    currency: str
    mean: float
    high: float | None = None
    low: float | None = None
    std: float | None = None
    analyst_count: int = 0
    median_age_days: float | None = None
    mean_30d_ago: float | None = None
    source: SourceRef


NewsSourceType = Literal[
    "factual_event", "management_claim", "analyst_opinion", "market_commentary", "social"
]


class NewsRecord(BaseModel):
    record_id: str
    published_at: datetime
    headline: str
    summary: str = ""
    source_name: str
    source_type: NewsSourceType
    url: str = ""
    sentiment: float = Field(default=0.0, ge=-1, le=1, description="Deterministic, not LLM")
    novelty: float = Field(default=1.0, ge=0, le=1, description="1 = new information")
    source: SourceRef


CatalystKind = Literal[
    "earnings", "investor_day", "regulatory", "product_launch", "contract",
    "capital_return", "refinancing", "m_and_a", "management_change",
    "index_change", "guidance", "filing",
]


class CatalystRecord(BaseModel):
    record_id: str
    kind: CatalystKind
    expected_date: date
    date_confirmed: bool = False
    description: str
    binary: bool = Field(default=False, description="Outcome dominates the stock either way")
    published_at: datetime | None = None
    resolved: bool = False
    outcome: str | None = None
    outcome_date: date | None = None
    source: SourceRef


class ShortInterestRecord(BaseModel):
    as_of: date
    shares_short: float
    pct_float: float | None = None
    days_to_cover: float | None = None
    source: SourceRef


class InsiderRecord(BaseModel):
    filed_at: datetime
    transaction_date: date
    insider_name: str
    insider_role: str
    kind: Literal["buy", "sell"]
    shares: float
    value: float | None = None
    source: SourceRef


class CorporateActionRecord(BaseModel):
    kind: Literal["split", "dividend", "ticker_change", "acquisition", "delisting"]
    ex_date: date
    factor: float | None = None  # splits
    amount: float | None = None  # dividends, per share, local ccy
    detail: str = ""
    source: SourceRef


# --------------------------------------------------------------------------
# Snapshot support types
# --------------------------------------------------------------------------


class InstrumentInfo(BaseModel):
    instrument_id: int
    ticker: str
    exchange: str
    market: str  # "US" | "UK" | "INDEX" | "SECTOR"
    name: str
    sector: str
    industry: str
    currency: str
    security_type: str = "common"
    is_shell: bool = False
    is_active: bool = True
    listed_at: date | None = None
    delisted_at: date | None = None
    shares_outstanding: float | None = None  # latest known <= as_of


class LiquidityStats(BaseModel):
    market_cap_local: float | None = None
    market_cap_base: float | None = None
    median_daily_traded_value_local: float | None = None
    median_daily_traded_value_base: float | None = None
    spread_estimate_bps: float | None = None
    price_staleness_days: int = 0  # trading days since last bar vs as_of


class PeerMetrics(BaseModel):
    """Pre-computed valuation/quality metrics for one peer at as_of, so
    engines never need to build recursive snapshots."""

    instrument_id: int
    ticker: str
    name: str
    sector: str
    industry: str
    metrics: dict[str, float] = Field(
        default_factory=dict,
        description="e.g. pe_ttm, ev_ebitda_ttm, fcf_yield, ev_sales, pb, "
        "gross_margin, revenue_growth_ttm, net_debt_ebitda, roic",
    )


class DataQualityFlags(BaseModel):
    completeness: float = Field(default=1.0, ge=0, le=1)
    price_staleness_days: int = 0
    latest_fundamental_age_days: int | None = None
    estimates_available: bool = True
    news_available: bool = True
    warnings: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class InstrumentSnapshot:
    """Frozen point-in-time bundle. Built ONLY by ``vigil.data.snapshot``.

    ``prices``: daily DataFrame indexed by ``date`` with columns
    ``open, high, low, close, adj_close, volume`` — bars with date <= as_of
    only, and split/dividend adjustment computed using actions with
    ex_date <= as_of only (a later split never rewrites history seen here).

    ``benchmark`` / ``sector_index``: adjusted close Series for the
    instrument's market benchmark and sector index, same date discipline.

    ``macro``: name -> Series of macro observations (rates, cpi_yoy,
    credit_spread, vix, ...), each filtered to publication <= as_of.
    """

    as_of: date
    info: InstrumentInfo
    prices: pd.DataFrame
    benchmark: pd.Series
    sector_index: pd.Series | None
    fx_to_base: float
    fx_as_of: date | None
    fundamentals: tuple[FundamentalRecord, ...]
    estimates: tuple[EstimateRecord, ...]
    target: TargetRecord | None
    news: tuple[NewsRecord, ...]
    catalysts: tuple[CatalystRecord, ...]
    short_interest: tuple[ShortInterestRecord, ...]
    insiders: tuple[InsiderRecord, ...]
    corporate_actions: tuple[CorporateActionRecord, ...]
    peers: tuple[PeerMetrics, ...]
    macro: dict[str, pd.Series] = field(default_factory=dict)
    liquidity: LiquidityStats = field(default_factory=LiquidityStats)
    quality: DataQualityFlags = field(default_factory=DataQualityFlags)

    # ---- convenience helpers (pure, derived from the frozen data) ----

    @property
    def last_close(self) -> float | None:
        if self.prices.empty:
            return None
        return float(self.prices["close"].iloc[-1])

    @property
    def last_price_date(self) -> date | None:
        if self.prices.empty:
            return None
        return self.prices.index[-1].date()  # type: ignore[union-attr]

    def quarterlies(self) -> list[FundamentalRecord]:
        """Quarterly records, restatements folded in, oldest first."""
        return _effective(self.fundamentals, "Q")

    def annuals(self) -> list[FundamentalRecord]:
        return _effective(self.fundamentals, "A")

    def ttm_sum(self, fieldname: str) -> float | None:
        """Sum of the last four quarterly values of a flow field."""
        qs = self.quarterlies()[-4:]
        if len(qs) < 4:
            return None
        vals = [getattr(q, fieldname) for q in qs]
        if any(v is None for v in vals):
            return None
        return float(sum(vals))

    def ttm_fcf(self) -> float | None:
        qs = self.quarterlies()[-4:]
        if len(qs) < 4:
            return None
        vals = [q.free_cash_flow for q in qs]
        if any(v is None for v in vals):
            return None
        return float(sum(v for v in vals if v is not None))

    def latest_fundamental(self) -> FundamentalRecord | None:
        qs = self.quarterlies()
        return qs[-1] if qs else None

    def market_cap_local(self) -> float | None:
        px = self.last_close
        sh = self.info.shares_outstanding
        if px is None or sh is None:
            return None
        return px * sh


def _effective(
    records: tuple[FundamentalRecord, ...], period_type: str
) -> list[FundamentalRecord]:
    """Fold restatements: for each period_end keep the latest-published
    visible record. Input is already point-in-time filtered by the builder."""
    by_period: dict[date, FundamentalRecord] = {}
    for rec in records:
        if rec.period_type != period_type:
            continue
        key = rec.restates_period_end or rec.period_end
        cur = by_period.get(key)
        if cur is None or rec.published_at > cur.published_at:
            by_period[key] = rec
    return [by_period[k] for k in sorted(by_period)]


# --------------------------------------------------------------------------
# Engine output
# --------------------------------------------------------------------------


class EngineResult(BaseModel):
    """What every engine returns. ``score=None`` = abstain (insufficient or
    inappropriate data) — never guess."""

    engine: str
    score: float | None = Field(default=None, ge=0, le=10)
    components: dict[str, float] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    data_quality: float = Field(default=1.0, ge=0, le=1)
    # Engine-specific structured extras consumed by signal rules
    # (e.g. technical support zones, valuation scenarios, value-trap flags).
    details: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Composite scoring output
# --------------------------------------------------------------------------


class GateResult(BaseModel):
    passed: bool
    failures: list[str] = Field(default_factory=list)
    reward_risk: float | None = None


class HorizonScore(BaseModel):
    horizon: Literal["short", "medium", "long"]
    opportunity: float = Field(ge=0, le=10)
    confidence: float = Field(ge=0, le=10)
    risk: float = Field(ge=0, le=10)
    components: dict[str, float] = Field(
        default_factory=dict,
        description="quality, growth, valuation, technical, momentum, "
        "sentiment, catalysts, balance_sheet, data_quality (each 0-10)",
    )
    abstained: bool = False
    abstain_reasons: list[str] = Field(default_factory=list)
    gate: GateResult | None = None
    explanation: list[str] = Field(
        default_factory=list, description="Deterministic per-factor contribution lines"
    )


class ScoreBundle(BaseModel):
    """Full multi-horizon assessment for one instrument on one scan date."""

    instrument_id: int
    as_of: date
    model_version: str
    horizons: dict[str, HorizonScore]
    best_fit_horizon: str | None = Field(
        default=None, description="Set only when evidence clearly favours one horizon"
    )
    engine_results: dict[str, EngineResult]
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Signals
# --------------------------------------------------------------------------


class SignalFamily(str, enum.Enum):
    DEEP_VALUE = "deep_value"
    QUALITY_COMPOUNDER = "quality_compounder"
    OVERSOLD_AT_SUPPORT = "oversold_at_support"
    CONSTRUCTIVE_PULLBACK = "constructive_pullback"
    BREAKOUT_CONTINUATION = "breakout_continuation"
    FUNDAMENTAL_INFLECTION = "fundamental_inflection"
    ESTIMATE_MOMENTUM = "estimate_momentum"
    WATCH_SETUP = "watch_setup"
    HOLD = "hold"
    AVOID = "avoid"
    TRIM = "trim"
    FULL_EXIT = "full_exit"
    THESIS_INVALIDATED = "thesis_invalidated"


class LifecycleState(str, enum.Enum):
    WATCHING = "WATCHING"
    TRIGGERED = "TRIGGERED"
    REINFORCED = "REINFORCED"
    WEAKENING = "WEAKENING"
    TRIM = "TRIM"
    EXITED = "EXITED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class Scenario(BaseModel):
    name: Literal["base", "bull", "bear"]
    price: float
    probability_note: str = Field(
        default="", description="Qualitative only — never a fabricated probability"
    )
    rationale: str


class EntryPlan(BaseModel):
    """Zones, not false precision."""

    zone_low: float | None = None
    zone_high: float | None = None
    stop: float | None = Field(default=None, description="Risk stop for short-term setups")
    conditions_before_entry: list[str] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)
    fundamental_invalidation: list[str] = Field(default_factory=list)
    target_low: float | None = None
    target_high: float | None = None
    scenarios: list[Scenario] = Field(default_factory=list)
    trim_conditions: list[str] = Field(default_factory=list)
    exit_conditions: list[str] = Field(default_factory=list)
    reward_risk: float | None = None


class SignalCandidate(BaseModel):
    """Produced by signal rules from a ScoreBundle; consumed by lifecycle."""

    family: SignalFamily
    horizon: Literal["short", "medium", "long"]
    instrument_id: int
    as_of: date
    state_hint: Literal["WATCHING", "TRIGGERED"] = "TRIGGERED"
    scores: HorizonScore
    entry_plan: EntryPlan
    thesis_keys: list[str] = Field(
        default_factory=list, description="Evidence keys that form the core thesis"
    )
    supporting: list[Evidence] = Field(default_factory=list)
    contradicting: list[Evidence] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
