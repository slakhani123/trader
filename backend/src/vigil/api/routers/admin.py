"""Operational endpoints: config, scan trigger, runs, model versions, audit,
notification deliveries."""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from vigil.api.deps import get_db, spawn_job
from vigil.api.routers._shared import run_dict
from vigil.config import Settings, get_settings
from vigil.models import AuditLog, ModelVersion, NotificationDelivery, ScoreRun

log = logging.getLogger("vigil.api")

router = APIRouter()


@router.get("/config")
def get_config_view() -> dict:
    """Sanitised settings — research configuration only, never credentials."""
    settings: Settings = get_settings()
    return {
        "universe": settings.universe.model_dump(),
        "horizons": settings.horizons.model_dump(),
        "gates": settings.gates.model_dump(),
        "alert_policy": settings.alert_policy.model_dump(),
        "risk_policy": settings.risk_policy.model_dump(),
        "scan": settings.scan.model_dump(),
        "base_currency": settings.base_currency,
        "model_version": settings.scoring_model_version,
    }


class ScanRequest(BaseModel):
    as_of: date | None = None


@router.post("/scan", status_code=202)
def post_scan(body: ScanRequest | None = None) -> dict:
    try:
        from vigil.jobs.scan import run_scan
    except ImportError as exc:  # pragma: no cover - depends on tree state
        raise HTTPException(status_code=503, detail="scanner not installed") from exc

    as_of = body.as_of if body and body.as_of else date.today()
    run_id = spawn_job(
        ScoreRun,
        lambda s: run_scan(s, as_of, trigger="manual"),
        name="vigil-scan",
    )
    return {"run_id": run_id}


@router.get("/runs")
def list_runs(
    limit: int = Query(default=50, ge=1, le=500), db: Session = Depends(get_db)
) -> dict:
    runs = db.execute(
        select(ScoreRun).order_by(ScoreRun.id.desc()).limit(limit)
    ).scalars().all()
    return {"items": [run_dict(r) for r in runs]}


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)) -> dict:
    run = db.get(ScoreRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
    return run_dict(run)


@router.get("/model-versions")
def list_model_versions(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(ModelVersion).order_by(ModelVersion.id.desc())).scalars().all()
    return [
        {
            "version": m.version,
            "created_at": m.created_at,
            "weights": m.weights,
            "config_hash": m.config_hash,
            "notes": m.notes,
            "active": m.active,
        }
        for m in rows
    ]


@router.get("/audit")
def list_audit(
    limit: int = Query(default=50, ge=1, le=500), db: Session = Depends(get_db)
) -> dict:
    rows = db.execute(
        select(AuditLog).order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit)
    ).scalars().all()
    return {
        "items": [
            {"at": a.at, "actor": a.actor, "action": a.action, "detail": a.detail} for a in rows
        ]
    }


@router.get("/notifications")
def list_notifications(
    limit: int = Query(default=50, ge=1, le=500), db: Session = Depends(get_db)
) -> dict:
    rows = db.execute(
        select(NotificationDelivery)
        .order_by(NotificationDelivery.created_at.desc(), NotificationDelivery.id.desc())
        .limit(limit)
    ).scalars().all()
    return {
        "items": [
            {
                "id": n.id,
                "alert_id": n.alert_id,
                "channel": n.channel,
                "created_at": n.created_at,
                "status": n.status,
                "detail": n.detail,
            }
            for n in rows
        ]
    }
