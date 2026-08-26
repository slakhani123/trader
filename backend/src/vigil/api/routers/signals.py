"""Signal lifecycle views."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vigil.api.deps import get_db, pagination
from vigil.api.routers._shared import signal_view
from vigil.models import Instrument, Signal

router = APIRouter()


@router.get("/signals")
def list_signals(
    state: str | None = None,
    family: str | None = None,
    active: bool | None = None,
    instrument_id: int | None = None,
    page: tuple[int, int] = Depends(pagination),
    db: Session = Depends(get_db),
) -> dict:
    limit, offset = page
    stmt = select(Signal, Instrument).join(Instrument, Signal.instrument_id == Instrument.id)
    if state:
        stmt = stmt.where(Signal.state == state)
    if family:
        stmt = stmt.where(Signal.family == family)
    if active is not None:
        stmt = stmt.where(Signal.active.is_(active))
    if instrument_id is not None:
        stmt = stmt.where(Signal.instrument_id == instrument_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(
        stmt.order_by(Signal.updated_at.desc(), Signal.id.desc()).offset(offset).limit(limit)
    ).all()
    return {"items": [signal_view(s, inst) for s, inst in rows], "total": total}


@router.get("/signals/{signal_id}")
def signal_detail(signal_id: int, db: Session = Depends(get_db)) -> dict:
    sig = db.get(Signal, signal_id)
    if sig is None:
        raise HTTPException(status_code=404, detail=f"unknown signal {signal_id}")
    inst = db.get(Instrument, sig.instrument_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="signal references unknown instrument")
    return signal_view(sig, inst)
