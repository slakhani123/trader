"""The full alert payload — every field the brief requires.

Stored immutably as ``Alert.payload``; rendered by the frontend and by
notification channels. Narrative fields are produced either by the
deterministic template composer or by the LLM synthesiser — in both cases
validated so that no numeric claim appears that is not present in the
evidence/score/price fields of this same payload.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from vigil.schemas.core import Evidence, Scenario

DISCLAIMER = (
    "Research support only — not a guarantee of any outcome and not "
    "personalised financial advice. Signals describe historical patterns "
    "and documented evidence; markets can and do behave otherwise."
)


class CompanyHeader(BaseModel):
    name: str
    ticker: str
    exchange: str
    market: str
    sector: str
    industry: str
    market_cap_local: float | None
    market_cap_base: float | None
    base_currency: str
    local_currency: str


class PriceStamp(BaseModel):
    price: float
    currency: str
    as_of_date: date
    bar_timestamp: datetime  # exact market-data timestamp of the last bar
    staleness_trading_days: int
    fx_to_base: float | None = None
    fx_as_of: date | None = None


class ScoreView(BaseModel):
    horizon: str
    opportunity: float
    confidence: float
    risk: float
    components: dict[str, float]
    explanation: list[str] = Field(default_factory=list)


class ThesisQA(BaseModel):
    """The seven questions every thesis must answer."""

    why_this_company: str
    why_now: str
    what_market_misunderstands: str
    what_would_prove_wrong: str
    expected_holding_period: str
    early_trim_or_exit_causes: str
    three_largest_risks: list[str]


class ValuationSummary(BaseModel):
    primary_multiple: str | None = None
    multiples: dict[str, float] = Field(default_factory=dict)
    vs_history_percentile: float | None = None
    vs_peers_note: str | None = None
    analyst_target: dict | None = None  # {mean, implied_upside_pct, count, dispersion_pct, median_age_days}
    fair_value_low: float | None = None
    fair_value_high: float | None = None


class TechnicalSummary(BaseModel):
    trend_state: str | None = None
    support_zones: list[dict] = Field(default_factory=list)
    resistance_levels: list[float] = Field(default_factory=list)
    rsi14: float | None = None
    atr_pct: float | None = None
    reward_risk: float | None = None
    notes: list[str] = Field(default_factory=list)


class CatalystView(BaseModel):
    kind: str
    date: date
    days: int
    confirmed: bool
    binary: bool
    description: str
    priced_in_pct: float | None = None


class SourceLine(BaseModel):
    provider: str
    source_type: str
    reference: str
    published_at: datetime | None
    freshness_days: float | None


class ChangeSincePrevious(BaseModel):
    previous_alert_at: datetime | None = None
    previous_state: str | None = None
    opportunity_delta: float | None = None
    risk_delta: float | None = None
    price_change_pct: float | None = None
    changed: list[str] = Field(default_factory=list)  # human-readable change lines


class AlertPayload(BaseModel):
    # Identity
    company: CompanyHeader
    signal_family: str
    lifecycle_state: str
    transition: str
    best_fit_horizon: str | None
    horizon: str
    priority: str

    # Market data
    price: PriceStamp

    # Scores (the alerted horizon plus the other horizons for context)
    scores: ScoreView
    all_horizons: dict[str, ScoreView]

    # What changed
    change: ChangeSincePrevious

    # Narrative
    thesis: ThesisQA
    thesis_summary: str
    narrative_source: str  # template | llm

    # Evidence
    supporting: list[Evidence]
    contradicting: list[Evidence]

    # Analysis blocks
    valuation: ValuationSummary
    technicals: TechnicalSummary
    catalysts: list[CatalystView]

    # Plan
    entry_zone: dict | None  # {low, high} — zone, never a point
    conditions_before_entry: list[str]
    invalidation_conditions: list[str]
    fundamental_invalidation: list[str]
    stop: float | None
    scenarios: list[Scenario]
    target_range: dict | None  # {low, high}
    trim_conditions: list[str]
    exit_conditions: list[str]

    # Warnings
    binary_event_warning: str | None
    data_warnings: list[str]
    missing_data: list[str]

    # Provenance
    sources: list[SourceLine]
    model_version: str
    generated_at: datetime
    disclaimer: str = DISCLAIMER
