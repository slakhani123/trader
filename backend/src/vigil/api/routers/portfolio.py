"""Portfolio positions and watchlist. Exposure limits come from settings."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from vigil.api.deps import get_db
from vigil.api.routers._shared import get_instrument_or_404, last_bar
from vigil.config import get_settings
from vigil.data import snapshot
from vigil.models import Instrument, PortfolioPosition, WatchlistItem

router = APIRouter()


@router.get("/portfolio")
def get_portfolio(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    today = date.today()
    rows = db.execute(
        select(PortfolioPosition, Instrument)
        .join(Instrument, PortfolioPosition.instrument_id == Instrument.id)
        .where(PortfolioPosition.active.is_(True))
        .order_by(PortfolioPosition.id)
    ).all()

    positions: list[dict] = []
    for pos, inst in rows:
        bar = last_bar(db, inst.id, today)
        last_price = float(bar.close) if bar else None
        rate, _ = snapshot.fx_to_base(db, inst.currency, settings.base_currency, today)
        value_base = last_price * pos.quantity * rate if last_price is not None else None
        unrealised = (
            (last_price / pos.avg_cost_local - 1.0) * 100.0
            if last_price is not None and pos.avg_cost_local
            else None
        )
        positions.append(
            {
                "id": pos.id,
                "instrument_id": inst.id,
                "ticker": inst.ticker,
                "name": inst.name,
                "sector": inst.sector,
                "quantity": pos.quantity,
                "avg_cost_local": pos.avg_cost_local,
                "currency": pos.currency,
                "opened_at": pos.opened_at,
                "last_price": last_price,
                "value_base": value_base,
                "weight_pct": None,
                "unrealised_pct": unrealised,
            }
        )

    total_value = sum(p["value_base"] for p in positions if p["value_base"] is not None)
    sector_values: dict[str, float] = {}
    for p in positions:
        if p["value_base"] is None or total_value <= 0:
            continue
        p["weight_pct"] = p["value_base"] / total_value * 100.0
        sector_values[p["sector"]] = sector_values.get(p["sector"], 0.0) + p["value_base"]
    sector_weights = {k: v / total_value * 100.0 for k, v in sector_values.items()}

    limits = {
        "max_position_exposure_pct": settings.risk_policy.max_position_exposure_pct,
        "max_sector_exposure_pct": settings.risk_policy.max_sector_exposure_pct,
    }
    breaches: list[str] = []
    for p in positions:
        w = p["weight_pct"]
        if w is not None and w > limits["max_position_exposure_pct"]:
            breaches.append(
                f"{p['ticker']} is {w:.1f}% of portfolio "
                f"(limit {limits['max_position_exposure_pct']:.1f}%)"
            )
    for sec, w in sector_weights.items():
        if w > limits["max_sector_exposure_pct"]:
            breaches.append(
                f"sector {sec or 'unclassified'} is {w:.1f}% of portfolio "
                f"(limit {limits['max_sector_exposure_pct']:.1f}%)"
            )

    return {
        "positions": positions,
        "totals": {
            "value_base": total_value,
            "sector_weights": sector_weights,
            "limits": limits,
            "breaches": breaches,
        },
    }


class PositionCreate(BaseModel):
    instrument_id: int
    quantity: float
    avg_cost_local: float
    opened_at: date


@router.post("/portfolio", status_code=201)
def add_position(body: PositionCreate, db: Session = Depends(get_db)) -> dict:
    inst = get_instrument_or_404(db, body.instrument_id)
    pos = PortfolioPosition(
        instrument_id=inst.id,
        quantity=body.quantity,
        avg_cost_local=body.avg_cost_local,
        currency=inst.currency,
        opened_at=body.opened_at,
    )
    db.add(pos)
    db.commit()
    return {"id": pos.id}


@router.delete("/portfolio/{position_id}")
def close_position(position_id: int, db: Session = Depends(get_db)) -> dict:
    pos = db.get(PortfolioPosition, position_id)
    if pos is None:
        raise HTTPException(status_code=404, detail=f"unknown position {position_id}")
    pos.active = False
    pos.closed_at = date.today()
    db.commit()
    return {"id": pos.id, "closed": True}


@router.get("/watchlist")
def get_watchlist(db: Session = Depends(get_db)) -> dict:
    rows = db.execute(
        select(WatchlistItem, Instrument)
        .join(Instrument, WatchlistItem.instrument_id == Instrument.id)
        .where(WatchlistItem.active.is_(True))
        .order_by(WatchlistItem.added_at.desc(), WatchlistItem.id.desc())
    ).all()
    return {
        "items": [
            {
                "id": item.id,
                "instrument_id": inst.id,
                "ticker": inst.ticker,
                "name": inst.name,
                "added_at": item.added_at,
                "notes": item.notes,
            }
            for item, inst in rows
        ]
    }


class WatchlistCreate(BaseModel):
    instrument_id: int
    notes: str | None = None


@router.post("/watchlist", status_code=201)
def add_watchlist_item(body: WatchlistCreate, db: Session = Depends(get_db)) -> dict:
    inst = get_instrument_or_404(db, body.instrument_id)
    item = WatchlistItem(instrument_id=inst.id, notes=body.notes or "")
    db.add(item)
    db.commit()
    return {"id": item.id}


@router.delete("/watchlist/{item_id}")
def remove_watchlist_item(item_id: int, db: Session = Depends(get_db)) -> dict:
    item = db.get(WatchlistItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"unknown watchlist item {item_id}")
    item.active = False
    db.commit()
    return {"id": item.id, "removed": True}
