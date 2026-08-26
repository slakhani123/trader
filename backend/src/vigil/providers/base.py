"""Provider adapter contracts.

Core logic never talks to a vendor: it talks to these protocols, and
``vigil.providers.registry`` resolves which adapter serves each capability
from configuration. Adding a vendor = implementing the relevant protocols
(see ``providers/template.py`` and docs/PROVIDERS.md).

Adapters return *normalised payload dataclasses*; ``vigil.data.ingest``
stores raw payloads for lineage, validates, deduplicates, and upserts the
normalised records. Adapters must be honest about capability gaps: a
capability an adapter does not support raises ``CapabilityUnavailable`` and
is surfaced in Data Health rather than silently returning nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol, runtime_checkable


class ProviderError(RuntimeError):
    """Transient or permanent provider failure (after retries)."""


class CapabilityUnavailable(ProviderError):
    """The selected provider does not supply this data type at all."""


# ---------------------------------------------------------------------------
# Normalised payloads (provider -> ingest). Deliberately dumb containers.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstrumentPayload:
    ticker: str
    exchange: str
    market: str
    name: str
    sector: str
    industry: str
    currency: str
    security_type: str = "common"
    is_shell: bool = False
    listed_at: date | None = None
    delisted_at: date | None = None
    delisting_reason: str = ""


@dataclass(frozen=True)
class BarPayload:
    ticker: str
    bar_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    currency: str


@dataclass(frozen=True)
class ActionPayload:
    ticker: str
    kind: str  # split|dividend|ticker_change|acquisition|delisting
    ex_date: date
    factor: float | None = None
    amount: float | None = None
    detail: str = ""
    published_at: datetime | None = None


@dataclass(frozen=True)
class FundamentalPayload:
    ticker: str
    period_end: date
    period_type: str  # Q|A
    published_at: datetime
    currency: str
    fields: dict  # keys mirror schemas.core.FundamentalRecord numerics
    is_restatement: bool = False
    restates_period_end: date | None = None
    source_reference: str = ""
    shares_outstanding: float | None = None


@dataclass(frozen=True)
class EstimatePayload:
    ticker: str
    as_of: date
    metric: str
    fiscal_label: str
    period_end: date
    mean: float
    high: float | None = None
    low: float | None = None
    analyst_count: int = 0
    mean_30d_ago: float | None = None
    mean_90d_ago: float | None = None
    up_revisions_30d: int = 0
    down_revisions_30d: int = 0


@dataclass(frozen=True)
class TargetPayload:
    ticker: str
    as_of: date
    currency: str
    mean: float
    high: float | None = None
    low: float | None = None
    std: float | None = None
    analyst_count: int = 0
    median_age_days: float | None = None
    mean_30d_ago: float | None = None


@dataclass(frozen=True)
class NewsPayload:
    ticker: str
    external_id: str
    published_at: datetime
    headline: str
    summary: str
    source_name: str
    source_type: str  # factual_event|management_claim|analyst_opinion|market_commentary|social
    url: str = ""
    sentiment: float = 0.0
    novelty: float = 1.0


@dataclass(frozen=True)
class CatalystPayload:
    ticker: str
    external_id: str
    kind: str
    expected_date: date
    description: str
    date_confirmed: bool = False
    binary: bool = False
    published_at: datetime | None = None
    resolved: bool = False
    outcome: str | None = None
    outcome_date: date | None = None
    url: str = ""


@dataclass(frozen=True)
class ShortInterestPayload:
    ticker: str
    as_of: date
    published_at: datetime
    shares_short: float
    pct_float: float | None = None
    days_to_cover: float | None = None


@dataclass(frozen=True)
class InsiderPayload:
    ticker: str
    filed_at: datetime
    transaction_date: date
    insider_name: str
    insider_role: str
    kind: str  # buy|sell
    shares: float
    value: float | None = None
    url: str = ""


@dataclass(frozen=True)
class MacroPayload:
    series_id: str
    obs_date: date
    value: float
    published_at: datetime


@dataclass(frozen=True)
class FxPayload:
    base_ccy: str
    quote_ccy: str
    rate_date: date
    rate: float


@dataclass(frozen=True)
class ProviderFetchResult:
    """What every fetch returns: records + the raw payload for lineage."""

    records: list
    raw: str = ""  # raw response body (JSON/text); empty for synthetic
    endpoint: str = ""
    retrieved_at: datetime | None = None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Capability protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class ReferenceProvider(Protocol):
    name: str

    def fetch_universe(self, markets: list[str]) -> ProviderFetchResult: ...


@runtime_checkable
class PriceProvider(Protocol):
    name: str

    def fetch_bars(self, ticker: str, start: date, end: date) -> ProviderFetchResult: ...

    def fetch_actions(self, ticker: str, start: date, end: date) -> ProviderFetchResult: ...


@runtime_checkable
class FundamentalsProvider(Protocol):
    name: str

    def fetch_fundamentals(self, ticker: str, start: date, end: date) -> ProviderFetchResult: ...


@runtime_checkable
class EstimatesProvider(Protocol):
    name: str

    def fetch_estimates(self, ticker: str, as_of: date) -> ProviderFetchResult: ...

    def fetch_targets(self, ticker: str, as_of: date) -> ProviderFetchResult: ...


@runtime_checkable
class NewsProvider(Protocol):
    name: str

    def fetch_news(self, ticker: str, start: date, end: date) -> ProviderFetchResult: ...

    def fetch_catalysts(self, ticker: str, as_of: date) -> ProviderFetchResult: ...


@runtime_checkable
class OwnershipProvider(Protocol):
    name: str

    def fetch_short_interest(self, ticker: str, start: date, end: date) -> ProviderFetchResult: ...

    def fetch_insiders(self, ticker: str, start: date, end: date) -> ProviderFetchResult: ...


@runtime_checkable
class MacroProvider(Protocol):
    name: str

    def fetch_macro(self, series_ids: list[str], start: date, end: date) -> ProviderFetchResult: ...

    def fetch_fx(self, pairs: list[tuple[str, str]], start: date, end: date) -> ProviderFetchResult: ...


@runtime_checkable
class OptionsProvider(Protocol):
    """Optional capability. No default implementation ships in v1 — the
    registry reports it unavailable unless the user configures a vendor."""

    name: str

    def fetch_options_summary(self, ticker: str, as_of: date) -> ProviderFetchResult: ...


@runtime_checkable
class HealthCheckable(Protocol):
    name: str

    def health_check(self) -> tuple[bool, str]: ...
