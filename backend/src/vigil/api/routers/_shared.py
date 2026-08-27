"""Serializers and small query helpers shared by several routers."""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from vigil.data import snapshot
from vigil.models import (
    Alert,
    Instrument,
    PortfolioPosition,
    PriceBar,
    ScoreRecord,
    ScoreRun,
    Signal,
    WatchlistItem,
)


def latest_completed_run(session: Session) -> ScoreRun | None:
    """'Latest completed run' = ScoreRun with status 'ok', highest id."""
    return session.execute(
        select(ScoreRun).where(ScoreRun.status == "ok").order_by(ScoreRun.id.desc()).limit(1)
    ).scalar_one_or_none()


def get_instrument_or_404(session: Session, instrument_id: int) -> Instrument:
    from fastapi import HTTPException

    inst = session.get(Instrument, instrument_id)
    if inst is None:
        raise HTTPException(status_code=404, detail=f"unknown instrument {instrument_id}")
    return inst


def instrument_dict(inst: Instrument) -> dict:
    return {
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
    }


def detail_str(detail: object) -> str | None:
    """JSON detail columns hold dicts; the API contract (and the dashboard,
    which renders this as text) wants a short human-readable string."""
    if detail is None or detail == "":
        return None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        parts = []
        for key, val in detail.items():
            if isinstance(val, dict) and ("inserted" in val or "skipped" in val):
                bit = f"{key}: {val.get('inserted', 0)} new"
                if val.get("skipped"):
                    bit += f", {val['skipped']} already stored"
                if val.get("failed_tickers"):
                    bit += f", {val['failed_tickers']} ticker(s) failed"
                if val.get("issues"):
                    bit += f", {len(val['issues'])} issue(s)"
                parts.append(bit)
            elif isinstance(val, dict | list):
                parts.append(f"{key}: {json.dumps(val, default=str)[:120]}")
            else:
                parts.append(f"{key}: {val}")
        return "; ".join(parts)[:500] or None
    return str(detail)


def run_dict(run: ScoreRun) -> dict:
    return {
        "id": run.id,
        "run_at": run.run_at,
        "as_of": run.as_of,
        "model_version": run.model_version,
        "trigger": run.trigger,
        "universe_size": run.universe_size,
        "scored": run.scored,
        "abstained": run.abstained,
        "status": run.status,
        "detail": detail_str(run.detail),
    }


def score_record_dict(rec: ScoreRecord) -> dict:
    return {
        "opportunity": rec.opportunity,
        "confidence": rec.confidence,
        "risk": rec.risk,
        "components": rec.components,
        "abstained": rec.abstained,
        "abstain_reasons": rec.abstain_reasons,
        "gate": rec.gate,
        "explanation": rec.explanation,
    }


def alert_summary(alert: Alert, inst: Instrument) -> dict:
    payload = alert.payload or {}
    scores = payload.get("scores") or {}
    return {
        "id": alert.id,
        "created_at": alert.created_at,
        "as_of": alert.as_of,
        "instrument_id": alert.instrument_id,
        "ticker": inst.ticker,
        "name": inst.name,
        "family": alert.family,
        "lifecycle_state": alert.lifecycle_state,
        "transition": alert.transition,
        "horizon": alert.horizon,
        "priority": alert.priority,
        "title": alert.title,
        "read": alert.read,
        "opportunity": scores.get("opportunity"),
        "confidence": scores.get("confidence"),
        "risk": scores.get("risk"),
        "thesis_summary": payload.get("thesis_summary", ""),
    }


def signal_view(sig: Signal, inst: Instrument) -> dict:
    return {
        "id": sig.id,
        "instrument_id": sig.instrument_id,
        "ticker": inst.ticker,
        "name": inst.name,
        "family": sig.family,
        "horizon": sig.horizon,
        "state": sig.state,
        "created_at": sig.created_at,
        "updated_at": sig.updated_at,
        "anchor_price": sig.anchor_price,
        "anchor_date": sig.anchor_date,
        "entry_plan": sig.entry_plan,
        "last_scores": sig.last_scores,
        "state_history": sig.state_history,
        "expires_at": sig.expires_at,
        "active": sig.active,
        "last_alert_at": sig.last_alert_at,
    }


def last_bar(session: Session, instrument_id: int, as_of: date) -> PriceBar | None:
    return session.execute(
        select(PriceBar)
        .where(PriceBar.instrument_id == instrument_id, PriceBar.bar_date <= as_of)
        .order_by(PriceBar.bar_date.desc())
        .limit(1)
    ).scalar_one_or_none()


def market_cap_base(
    session: Session, inst: Instrument, base_currency: str, as_of: date
) -> float | None:
    """Cheap market cap in base currency: last close x latest shares x FX."""
    bar = last_bar(session, inst.id, as_of)
    if bar is None:
        return None
    shares = snapshot.latest_shares(session, inst.id, as_of)
    if not shares:
        return None
    rate, _ = snapshot.fx_to_base(session, inst.currency, base_currency, as_of)
    return float(bar.close) * float(shares) * rate


def owned_instrument_ids(session: Session) -> set[int]:
    return set(
        session.execute(
            select(PortfolioPosition.instrument_id).where(PortfolioPosition.active.is_(True))
        ).scalars()
    )


def watchlisted_instrument_ids(session: Session) -> set[int]:
    return set(
        session.execute(
            select(WatchlistItem.instrument_id).where(WatchlistItem.active.is_(True))
        ).scalars()
    )
