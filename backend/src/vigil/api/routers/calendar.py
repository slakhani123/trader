"""Upcoming catalyst calendar across the universe."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from vigil.api.deps import get_db
from vigil.models import Catalyst, Instrument

router = APIRouter()


@router.get("/calendar")
def get_calendar(
    days: int = Query(default=60, ge=1, le=730),
    binary_only: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    today = date.today()
    stmt = (
        select(Catalyst, Instrument)
        .join(Instrument, Catalyst.instrument_id == Instrument.id)
        .where(
            Catalyst.expected_date >= today,
            Catalyst.expected_date <= today + timedelta(days=days),
            Catalyst.resolved.is_(False),
        )
        .order_by(Catalyst.expected_date, Instrument.ticker)
    )
    if binary_only:
        stmt = stmt.where(Catalyst.binary.is_(True))
    rows = db.execute(stmt).all()
    return {
        "items": [
            {
                "instrument_id": inst.id,
                "ticker": inst.ticker,
                "name": inst.name,
                "kind": cat.kind,
                "expected_date": cat.expected_date,
                "days": (cat.expected_date - today).days,
                "date_confirmed": cat.date_confirmed,
                "binary": cat.binary,
                "description": cat.description,
            }
            for cat, inst in rows
        ]
    }
