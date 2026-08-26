"""Alert feed, alert detail (full payload), read/unread bookkeeping."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vigil.api.deps import get_db, pagination
from vigil.api.routers._shared import alert_summary
from vigil.models import Alert, Instrument

router = APIRouter()


@router.get("/alerts")
def list_alerts(
    family: str | None = None,
    state: str | None = None,
    priority: str | None = None,
    horizon: str | None = None,
    unread_only: bool = False,
    instrument_id: int | None = None,
    since: datetime | None = None,
    page: tuple[int, int] = Depends(pagination),
    db: Session = Depends(get_db),
) -> dict:
    limit, offset = page
    stmt = select(Alert, Instrument).join(Instrument, Alert.instrument_id == Instrument.id)
    if family:
        stmt = stmt.where(Alert.family == family)
    if state:
        stmt = stmt.where(Alert.lifecycle_state == state)
    if priority:
        stmt = stmt.where(Alert.priority == priority)
    if horizon:
        stmt = stmt.where(Alert.horizon == horizon)
    if unread_only:
        stmt = stmt.where(Alert.read.is_(False))
    if instrument_id is not None:
        stmt = stmt.where(Alert.instrument_id == instrument_id)
    if since is not None:
        stmt = stmt.where(Alert.created_at >= since)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(
        stmt.order_by(Alert.created_at.desc(), Alert.id.desc()).offset(offset).limit(limit)
    ).all()
    return {"items": [alert_summary(a, inst) for a, inst in rows], "total": total}


def _get_alert(db: Session, alert_id: str) -> Alert:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"unknown alert {alert_id}")
    return alert


@router.get("/alerts/{alert_id}")
def alert_detail(alert_id: str, db: Session = Depends(get_db)) -> dict:
    alert = _get_alert(db, alert_id)
    inst = db.get(Instrument, alert.instrument_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="alert references unknown instrument")
    summary = alert_summary(alert, inst)
    summary["payload"] = alert.payload  # verbatim, as stored
    return summary


@router.post("/alerts/{alert_id}/read")
def mark_read(alert_id: str, db: Session = Depends(get_db)) -> dict:
    alert = _get_alert(db, alert_id)
    alert.read = True
    db.commit()
    return {"id": alert.id, "read": True}


@router.post("/alerts/{alert_id}/unread")
def mark_unread(alert_id: str, db: Session = Depends(get_db)) -> dict:
    alert = _get_alert(db, alert_id)
    alert.read = False
    db.commit()
    return {"id": alert.id, "read": False}
