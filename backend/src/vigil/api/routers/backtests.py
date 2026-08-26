"""Backtest runs: listing, detail with trades, and background execution."""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from vigil.api.deps import get_db, spawn_job
from vigil.models import BacktestRun, BacktestTrade, Instrument

log = logging.getLogger("vigil.api")

router = APIRouter()

MAX_TRADES = 2000


def _run_summary(run: BacktestRun) -> dict:
    return {
        "id": run.id,
        "created_at": run.created_at,
        "name": run.name,
        "model_version": run.model_version,
        "start_date": run.start_date,
        "end_date": run.end_date,
        "holdout_start": run.holdout_start,
        "status": run.status,
        "metrics": run.metrics,
    }


@router.get("/backtests")
def list_backtests(db: Session = Depends(get_db)) -> dict:
    runs = db.execute(select(BacktestRun).order_by(BacktestRun.id.desc())).scalars().all()
    return {"items": [_run_summary(r) for r in runs]}


@router.get("/backtests/{backtest_id}")
def backtest_detail(backtest_id: int, db: Session = Depends(get_db)) -> dict:
    run = db.get(BacktestRun, backtest_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown backtest {backtest_id}")
    trades = db.execute(
        select(BacktestTrade, Instrument)
        .join(Instrument, BacktestTrade.instrument_id == Instrument.id)
        .where(BacktestTrade.run_id == run.id)
        .order_by(BacktestTrade.signal_date.desc(), BacktestTrade.id.desc())
        .limit(MAX_TRADES)
    ).all()
    out = _run_summary(run)
    out.update(
        {
            "config": run.config,
            "by_bucket": run.by_bucket,
            "calibration": run.calibration,
            "notes": run.notes,
            "trades": [
                {
                    "instrument_id": t.instrument_id,
                    "ticker": inst.ticker,
                    "family": t.family,
                    "horizon": t.horizon,
                    "signal_date": t.signal_date,
                    "entry_date": t.entry_date,
                    "entry_price": t.entry_price,
                    "exit_date": t.exit_date,
                    "exit_price": t.exit_price,
                    "exit_reason": t.exit_reason,
                    "holding_days": t.holding_days,
                    "return_pct": t.return_pct,
                    "benchmark_return_pct": t.benchmark_return_pct,
                    "mae_pct": t.mae_pct,
                    "mfe_pct": t.mfe_pct,
                    "costs_bps": t.costs_bps,
                    "opportunity": t.opportunity,
                    "confidence": t.confidence,
                    "risk": t.risk,
                }
                for t, inst in trades
            ],
        }
    )
    return out


class BacktestRequest(BaseModel):
    name: str | None = None
    start: date
    end: date | None = None
    holdout_start: date | None = None
    step_days: int | None = None


@router.post("/backtests", status_code=202)
def post_backtest(body: BacktestRequest) -> dict:
    try:
        from vigil.backtest.engine import run_backtest
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="backtester not installed") from exc

    end = body.end or date.today()
    name = body.name or f"backtest {body.start.isoformat()}..{end.isoformat()}"
    kwargs: dict = {"name": name, "holdout_start": body.holdout_start}
    if body.step_days is not None:
        kwargs["step_days"] = body.step_days
    backtest_id = spawn_job(
        BacktestRun,
        lambda s: run_backtest(s, body.start, end, **kwargs),
        name="vigil-backtest",
    )
    return {"backtest_id": backtest_id}
