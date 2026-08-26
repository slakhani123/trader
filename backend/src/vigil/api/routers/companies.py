"""Company drill-down: header + latest assessment, prices, financials,
engine outputs, peers, alerts, signals."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Query
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from vigil.api.deps import get_db
from vigil.api.routers._shared import (
    alert_summary,
    get_instrument_or_404,
    last_bar,
    latest_completed_run,
    score_record_dict,
    signal_view,
)
from vigil.config import get_settings
from vigil.data import snapshot
from vigil.data.snapshot import load_price_frame
from vigil.models import (
    Alert,
    EngineOutput,
    FundamentalReport,
    Instrument,
    PortfolioPosition,
    PriceBar,
    ScoreBundleRow,
    ScoreRecord,
    ScoreRun,
    Signal,
    WatchlistItem,
)

router = APIRouter()


def _latest_assessment(db: Session, instrument_id: int) -> dict | None:
    row = db.execute(
        select(ScoreBundleRow)
        .join(ScoreRun, ScoreBundleRow.run_id == ScoreRun.id)
        .where(ScoreBundleRow.instrument_id == instrument_id, ScoreRun.status == "ok")
        .order_by(ScoreRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    records = db.execute(
        select(ScoreRecord).where(ScoreRecord.bundle_id == row.id)
    ).scalars().all()
    return {
        "run_id": row.run_id,
        "as_of": row.as_of,
        "best_fit_horizon": row.best_fit_horizon,
        "horizons": {rec.horizon: score_record_dict(rec) for rec in records},
        "warnings": row.warnings,
    }


def _liquidity(db: Session, inst: Instrument, as_of: date) -> dict:
    settings = get_settings()
    bar = last_bar(db, inst.id, as_of)
    if bar is None:
        return {
            "market_cap_base": None,
            "median_daily_traded_value_base": None,
            "price_staleness_days": None,
        }
    rate, _ = snapshot.fx_to_base(db, inst.currency, settings.base_currency, as_of)
    shares = snapshot.latest_shares(db, inst.id, as_of)
    mcap = float(bar.close) * float(shares) * rate if shares else None
    window = settings.universe.liquidity_window_days
    tail = db.execute(
        select(PriceBar.close, PriceBar.volume)
        .where(PriceBar.instrument_id == inst.id, PriceBar.bar_date <= as_of)
        .order_by(PriceBar.bar_date.desc())
        .limit(window)
    ).all()
    traded = float(np.median([c * v for c, v in tail])) * rate if tail else None
    return {
        "market_cap_base": mcap,
        "median_daily_traded_value_base": traded,
        "price_staleness_days": max(0, int(np.busday_count(bar.bar_date, as_of))),
    }


@router.get("/companies/{instrument_id}")
def company_detail(instrument_id: int, db: Session = Depends(get_db)) -> dict:
    inst = get_instrument_or_404(db, instrument_id)
    today = date.today()
    owned = db.execute(
        select(
            exists().where(
                PortfolioPosition.instrument_id == instrument_id,
                PortfolioPosition.active.is_(True),
            )
        )
    ).scalar_one()
    watchlisted = db.execute(
        select(
            exists().where(
                WatchlistItem.instrument_id == instrument_id, WatchlistItem.active.is_(True)
            )
        )
    ).scalar_one()
    return {
        "instrument": {
            "id": inst.id,
            "ticker": inst.ticker,
            "exchange": inst.exchange,
            "market": inst.market,
            "name": inst.name,
            "sector": inst.sector,
            "industry": inst.industry,
            "currency": inst.currency,
            "security_type": inst.security_type,
            "is_active": inst.is_active,
            "delisted_at": inst.delisted_at,
        },
        "latest": _latest_assessment(db, instrument_id),
        "liquidity": _liquidity(db, inst, today),
        "watchlisted": bool(watchlisted),
        "owned": bool(owned),
    }


@router.get("/companies/{instrument_id}/prices")
def company_prices(
    instrument_id: int,
    days: int = Query(default=730, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> dict:
    get_instrument_or_404(db, instrument_id)
    today = date.today()
    frame = load_price_frame(db, instrument_id, today)
    cutoff = today - timedelta(days=days)
    if not frame.empty:
        frame = frame.loc[frame.index >= pd.Timestamp(cutoff)]
    bars = [
        {
            "date": idx.date(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "adj_close": float(row["adj_close"]),
            "volume": float(row["volume"]),
        }
        for idx, row in frame.iterrows()
    ]
    alerts = db.execute(
        select(Alert)
        .where(Alert.instrument_id == instrument_id, Alert.as_of >= cutoff)
        .order_by(Alert.as_of)
    ).scalars().all()
    markers = [
        {
            "date": a.as_of,
            "family": a.family,
            "state": a.lifecycle_state,
            "transition": a.transition,
            "alert_id": a.id,
        }
        for a in alerts
    ]
    return {"bars": bars, "markers": markers}


@router.get("/companies/{instrument_id}/financials")
def company_financials(instrument_id: int, db: Session = Depends(get_db)) -> dict:
    """Restatement-folded, latest-visible quarterly view."""
    get_instrument_or_404(db, instrument_id)
    rows = db.execute(
        select(FundamentalReport)
        .where(
            FundamentalReport.instrument_id == instrument_id,
            FundamentalReport.period_type == "Q",
        )
        .order_by(FundamentalReport.period_end, FundamentalReport.published_at)
    ).scalars().all()
    folded: dict[date, FundamentalReport] = {}
    for r in rows:
        key = r.restates_period_end or r.period_end
        cur = folded.get(key)
        if cur is None or r.published_at > cur.published_at:
            folded[key] = r

    quarters = []
    for period_end in sorted(folded):
        r = folded[period_end]
        p = r.payload or {}
        revenue = p.get("revenue")

        def pct(value: float | None, rev: float | None = revenue) -> float | None:
            if value is None or not rev:
                return None
            return round(value / rev * 100.0, 2)

        ocf, capex = p.get("operating_cash_flow"), p.get("capex")
        fcf = ocf - capex if ocf is not None and capex is not None else None
        debt, cash = p.get("total_debt"), p.get("cash_and_equivalents")
        net_debt = debt - cash if debt is not None and cash is not None else None
        quarters.append(
            {
                "period_end": period_end,
                "published_at": r.published_at,
                "revenue": revenue,
                "gross_margin_pct": pct(p.get("gross_profit")),
                "operating_margin_pct": pct(p.get("operating_income")),
                "net_income": p.get("net_income"),
                "eps_diluted": p.get("eps_diluted"),
                "fcf": fcf,
                "net_debt": net_debt,
                "is_restatement": r.is_restatement,
            }
        )
    return {"quarters": quarters}


@router.get("/companies/{instrument_id}/engines")
def company_engines(
    instrument_id: int,
    run_id: int | None = None,
    db: Session = Depends(get_db),
) -> dict:
    get_instrument_or_404(db, instrument_id)
    if run_id is None:
        run = latest_completed_run(db)
        if run is None:
            return {"engines": []}
        run_id = run.id
    rows = db.execute(
        select(EngineOutput)
        .where(EngineOutput.run_id == run_id, EngineOutput.instrument_id == instrument_id)
        .order_by(EngineOutput.engine)
    ).scalars().all()
    return {
        "engines": [
            {
                "engine": e.engine,
                "score": e.score,
                "components": e.components,
                "evidence": e.evidence,
                "warnings": e.warnings,
                "data_quality": e.data_quality,
                "details": e.details,
            }
            for e in rows
        ]
    }


@router.get("/companies/{instrument_id}/peers")
def company_peers(instrument_id: int, db: Session = Depends(get_db)) -> dict:
    """Peer metrics from the same logic the snapshot builder uses."""
    inst = get_instrument_or_404(db, instrument_id)
    if not inst.sector:
        return {"peers": []}
    today = date.today()
    peer_rows = db.execute(
        select(Instrument).where(
            Instrument.sector == inst.sector,
            Instrument.id != inst.id,
            Instrument.security_type == "common",
        )
    ).scalars().all()
    peers = []
    for peer in peer_rows[: snapshot.MAX_PEERS * 2]:
        pm = snapshot._basic_peer_metrics(db, peer, today)
        if pm is not None:
            peers.append(
                {
                    "instrument_id": pm.instrument_id,
                    "ticker": pm.ticker,
                    "name": pm.name,
                    "metrics": pm.metrics,
                }
            )
        if len(peers) >= snapshot.MAX_PEERS:
            break
    return {"peers": peers}


@router.get("/companies/{instrument_id}/alerts")
def company_alerts(
    instrument_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    inst = get_instrument_or_404(db, instrument_id)
    rows = db.execute(
        select(Alert)
        .where(Alert.instrument_id == instrument_id)
        .order_by(Alert.created_at.desc())
        .limit(limit)
    ).scalars().all()
    return {"items": [alert_summary(a, inst) for a in rows]}


@router.get("/companies/{instrument_id}/signals")
def company_signals(instrument_id: int, db: Session = Depends(get_db)) -> dict:
    inst = get_instrument_or_404(db, instrument_id)
    rows = db.execute(
        select(Signal)
        .where(Signal.instrument_id == instrument_id)
        .order_by(Signal.created_at.desc(), Signal.id.desc())
    ).scalars().all()
    return {"items": [signal_view(s, inst) for s in rows]}
