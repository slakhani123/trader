"""Health and data-health endpoints. ``/api/health`` is the only unauthenticated route."""

from __future__ import annotations

from datetime import date

import numpy as np
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vigil import __version__
from vigil.api.deps import get_db, require_auth
from vigil.api.routers._shared import detail_str
from vigil.db import get_session_factory
from vigil.models import Instrument, JobRun, PriceBar, ProviderHealthRecord, ScoreRun

router = APIRouter()


@router.get("/health")
def health() -> dict:
    db_status = "ok"
    try:
        with get_session_factory()() as session:
            session.execute(select(1))
    except Exception:
        db_status = "error"
    return {"status": "ok", "version": __version__, "db": db_status}


@router.get("/health/data", dependencies=[Depends(require_auth)])
def health_data(db: Session = Depends(get_db)) -> dict:
    latest_ids = (
        select(func.max(ProviderHealthRecord.id))
        .group_by(ProviderHealthRecord.provider, ProviderHealthRecord.capability)
        .scalar_subquery()
    )
    providers = db.execute(
        select(ProviderHealthRecord)
        .where(ProviderHealthRecord.id.in_(latest_ids))
        .order_by(ProviderHealthRecord.provider, ProviderHealthRecord.capability)
    ).scalars().all()

    jobs = db.execute(
        select(JobRun).order_by(JobRun.started_at.desc(), JobRun.id.desc()).limit(20)
    ).scalars().all()

    instruments = db.execute(select(func.count()).select_from(Instrument)).scalar_one()
    last_bar_date = db.execute(select(func.max(PriceBar.bar_date))).scalar_one_or_none()
    last_run_at = db.execute(select(func.max(ScoreRun.run_at))).scalar_one_or_none()
    staleness = None
    if last_bar_date is not None:
        staleness = max(0, int(np.busday_count(last_bar_date, date.today())))

    return {
        "providers": [
            {
                "provider": p.provider,
                "capability": p.capability,
                "ok": p.ok,
                "configured": p.configured,
                "message": p.message,
                "checked_at": p.checked_at,
            }
            for p in providers
        ],
        "jobs": [
            {
                "job_name": j.job_name,
                "started_at": j.started_at,
                "finished_at": j.finished_at,
                "status": j.status,
                "detail": detail_str(j.detail),
            }
            for j in jobs
        ],
        "data": {
            "instruments": instruments,
            "last_bar_date": last_bar_date,
            "last_run_at": last_run_at,
            "price_staleness_days": staleness,
        },
    }
