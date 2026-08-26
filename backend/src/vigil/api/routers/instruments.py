"""Universe listing."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from vigil.api.deps import get_db, pagination
from vigil.api.routers._shared import instrument_dict
from vigil.models import Instrument

router = APIRouter()


@router.get("/instruments")
def list_instruments(
    market: str | None = None,
    sector: str | None = None,
    q: str | None = None,
    active: bool | None = None,
    page: tuple[int, int] = Depends(pagination),
    db: Session = Depends(get_db),
) -> dict:
    limit, offset = page
    stmt = select(Instrument)
    if market:
        stmt = stmt.where(Instrument.market == market)
    if sector:
        stmt = stmt.where(Instrument.sector == sector)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Instrument.ticker.ilike(like), Instrument.name.ilike(like)))
    if active is not None:
        stmt = stmt.where(Instrument.is_active.is_(active))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(
        stmt.order_by(Instrument.ticker, Instrument.id).offset(offset).limit(limit)
    ).scalars().all()
    return {"items": [instrument_dict(i) for i in rows], "total": total}
