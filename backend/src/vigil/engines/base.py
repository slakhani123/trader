"""Shared helpers for research engines."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time

from vigil.config import Settings
from vigil.schemas.core import (
    Direction,
    EngineResult,
    Evidence,
    InstrumentSnapshot,
    Pillar,
    SourceRef,
)

ENGINE_NAMES = (
    "quality", "valuation", "technical", "momentum", "sentiment", "catalyst", "regime",
)


def derived_ref(
    snapshot: InstrumentSnapshot, formula: str, based_on: SourceRef | None = None
) -> SourceRef:
    """SourceRef for a metric WE computed. ``formula`` names the calculation
    (documented in docs/FORMULAS.md); provenance timestamps flow through from
    the underlying record when given."""
    return SourceRef(
        provider="vigil",
        source_type="derived",
        reference=f"formula:{formula}",
        published_at=based_on.published_at if based_on else None,
        retrieved_at=based_on.retrieved_at if based_on else None,
        freshness_days=based_on.freshness_days if based_on else None,
    )


def price_ref(snapshot: InstrumentSnapshot) -> SourceRef:
    last = snapshot.last_price_date
    return SourceRef(
        provider="market-data",
        source_type="price",
        reference=f"bars:{snapshot.info.ticker}",
        published_at=datetime.combine(last, time(21, 0)) if last else None,
        freshness_days=float(snapshot.liquidity.price_staleness_days),
    )


def ev(
    snapshot: InstrumentSnapshot,
    key: str,
    statement: str,
    value: float | str | None,
    direction: Direction,
    pillar: Pillar,
    source: SourceRef,
) -> Evidence:
    return Evidence(
        key=key,
        statement=statement,
        value=value,
        direction=direction,
        pillar=pillar,
        source=source,
        as_of=snapshot.as_of,
    )


# ---------------------------------------------------------------------------
# Sector-aware classification
# ---------------------------------------------------------------------------

SectorClass = str  # general | bank | insurer | reit | commodity | early_stage


def sector_class(snapshot: InstrumentSnapshot) -> SectorClass:
    """Which metric family applies. Banks/insurers/REITs/commodity producers
    and pre-profit companies must not be scored on generic metrics."""
    industry = snapshot.info.industry.lower()
    sector = snapshot.info.sector.lower()
    if "bank" in industry:
        return "bank"
    if "insur" in industry:
        return "insurer"
    if "reit" in industry or sector == "real estate":
        return "reit"
    if any(k in industry for k in ("oil", "gas", "mining", "metals", "commodit")):
        return "commodity"
    qs = snapshot.quarterlies()
    if len(qs) >= 4:
        ni = [q.net_income for q in qs[-4:] if q.net_income is not None]
        rev = [q.revenue for q in qs[-4:] if q.revenue is not None]
        if ni and rev and sum(ni) < 0 and sum(rev) < 400e6:
            return "early_stage"
    return "general"


def abstain(engine: str, reason: str, data_quality: float = 0.0) -> EngineResult:
    return EngineResult(
        engine=engine, score=None, warnings=[reason], data_quality=data_quality
    )


def run_all_engines(
    snapshot: InstrumentSnapshot, settings: Settings
) -> dict[str, EngineResult]:
    """Run every engine; an engine crash becomes an abstention with a
    warning rather than sinking the whole scan."""
    import vigil.engines.catalyst as catalyst
    import vigil.engines.momentum as momentum
    import vigil.engines.quality as quality
    import vigil.engines.regime as regime
    import vigil.engines.sentiment as sentiment
    import vigil.engines.technical as technical
    import vigil.engines.valuation as valuation

    fns: dict[str, Callable[[InstrumentSnapshot, Settings], EngineResult]] = {
        "quality": quality.analyse,
        "valuation": valuation.analyse,
        "technical": technical.analyse,
        "momentum": momentum.analyse,
        "sentiment": sentiment.analyse,
        "catalyst": catalyst.analyse,
        "regime": regime.analyse,
    }
    results: dict[str, EngineResult] = {}
    for name, fn in fns.items():
        try:
            results[name] = fn(snapshot, settings)
        except Exception as exc:
            results[name] = abstain(name, f"engine error: {exc!r}")
    return results
