"""Ranked opportunities from the latest completed scoring run."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from vigil.api.deps import get_db, pagination
from vigil.api.routers._shared import (
    latest_completed_run,
    market_cap_base,
    owned_instrument_ids,
    watchlisted_instrument_ids,
)
from vigil.config import get_settings
from vigil.models import Catalyst, Instrument, ScoreBundleRow, ScoreRecord, Signal

router = APIRouter()


@router.get("/opportunities")
def list_opportunities(
    horizon: Literal["short", "medium", "long"] = "medium",
    market: str | None = None,
    sector: str | None = None,
    family: str | None = None,
    min_opportunity: float | None = None,
    min_confidence: float | None = None,
    max_risk: float | None = None,
    gated_only: bool = False,
    owned: bool | None = None,
    watchlisted: bool | None = None,
    catalyst_within_days: int | None = None,
    page: tuple[int, int] = Depends(pagination),
    db: Session = Depends(get_db),
) -> dict:
    limit, offset = page
    run = latest_completed_run(db)
    if run is None:
        return {"as_of": None, "run_id": None, "items": [], "total": 0}

    stmt = (
        select(ScoreRecord, Instrument, ScoreBundleRow)
        .join(Instrument, ScoreRecord.instrument_id == Instrument.id)
        .join(ScoreBundleRow, ScoreRecord.bundle_id == ScoreBundleRow.id)
        .where(ScoreRecord.run_id == run.id, ScoreRecord.horizon == horizon)
    )
    if market:
        stmt = stmt.where(Instrument.market == market)
    if sector:
        stmt = stmt.where(Instrument.sector == sector)
    if min_opportunity is not None:
        stmt = stmt.where(ScoreRecord.opportunity >= min_opportunity)
    if min_confidence is not None:
        stmt = stmt.where(ScoreRecord.confidence >= min_confidence)
    if max_risk is not None:
        stmt = stmt.where(ScoreRecord.risk <= max_risk)
    rows = db.execute(stmt).all()

    owned_ids = owned_instrument_ids(db)
    watch_ids = watchlisted_instrument_ids(db)
    active_signals: dict[int, list[dict]] = {}
    for sig in db.execute(
        select(Signal).where(Signal.active.is_(True)).order_by(Signal.id)
    ).scalars():
        active_signals.setdefault(sig.instrument_id, []).append(
            {"family": sig.family, "state": sig.state}
        )
    catalyst_ids: set[int] | None = None
    if catalyst_within_days is not None:
        window_end = run.as_of + timedelta(days=catalyst_within_days)
        catalyst_ids = set(
            db.execute(
                select(Catalyst.instrument_id).where(
                    Catalyst.expected_date >= run.as_of,
                    Catalyst.expected_date <= window_end,
                )
            ).scalars()
        )

    items: list[dict] = []
    instruments: dict[int, Instrument] = {}
    for rec, inst, bundle in rows:
        gate_passed = bool((rec.gate or {}).get("passed", False))
        if gated_only and not gate_passed:
            continue
        signals = active_signals.get(inst.id, [])
        if family and not any(s["family"] == family for s in signals):
            continue
        is_owned = inst.id in owned_ids
        if owned is not None and is_owned is not owned:
            continue
        is_watchlisted = inst.id in watch_ids
        if watchlisted is not None and is_watchlisted is not watchlisted:
            continue
        if catalyst_ids is not None and inst.id not in catalyst_ids:
            continue
        instruments[inst.id] = inst
        items.append(
            {
                "instrument_id": inst.id,
                "ticker": inst.ticker,
                "name": inst.name,
                "market": inst.market,
                "sector": inst.sector,
                "horizon": rec.horizon,
                "opportunity": rec.opportunity,
                "confidence": rec.confidence,
                "risk": rec.risk,
                "components": rec.components,
                "best_fit_horizon": bundle.best_fit_horizon,
                "gate_passed": gate_passed,
                "abstained": rec.abstained,
                "active_signals": signals,
                "market_cap_base": None,
                "owned": is_owned,
                "watchlisted": is_watchlisted,
            }
        )

    items.sort(key=lambda d: d["opportunity"], reverse=True)
    total = len(items)
    page_items = items[offset : offset + limit]
    base_ccy = get_settings().base_currency
    for item in page_items:  # cheap per-page enrichment; null is tolerated
        item["market_cap_base"] = market_cap_base(
            db, instruments[item["instrument_id"]], base_ccy, run.as_of
        )
    return {"as_of": run.as_of, "run_id": run.id, "items": page_items, "total": total}
