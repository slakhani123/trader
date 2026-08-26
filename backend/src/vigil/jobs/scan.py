"""The scan: snapshot → engines → composite scores → signals → alerts.

Everything an alert later claims is persisted here: engine outputs with
evidence, per-horizon scores with explanations, gate results, and the
immutable alert payloads. Scores are computed ownership-blind; portfolio
context is applied only afterwards for hold/trim/exit stances.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from vigil.alerts.builder import build_alert
from vigil.alerts.notify import deliver
from vigil.config import Settings, get_settings
from vigil.data.snapshot import SnapshotBuildError, build_snapshot
from vigil.engines import run_all_engines
from vigil.models import (
    AuditLog,
    Instrument,
    PortfolioPosition,
    ScoreBundleRow,
    ScoreRecord,
    ScoreRun,
    Signal,
)
from vigil.models.scoring import EngineOutput
from vigil.schemas.core import ScoreBundle
from vigil.scoring.composite import score_instrument
from vigil.scoring.gates import universe_eligible
from vigil.scoring.weights import config_hash, ensure_model_version
from vigil.signals.lifecycle import sync_signals
from vigil.signals.rules import generate_candidates, portfolio_rules

log = logging.getLogger(__name__)


def _portfolio_weights(
    session: Session, as_of: date, settings: Settings
) -> tuple[dict[int, float], dict[str, float]]:
    """(instrument_id -> weight %, sector -> weight %) in base currency."""
    from vigil.data.snapshot import fx_to_base, load_price_frame

    positions = session.execute(
        select(PortfolioPosition).where(PortfolioPosition.active.is_(True))
    ).scalars().all()
    values: dict[int, float] = {}
    sectors: dict[str, float] = {}
    for pos in positions:
        inst = session.get(Instrument, pos.instrument_id)
        if inst is None:
            continue
        frame = load_price_frame(session, pos.instrument_id, as_of)
        if frame.empty:
            continue
        rate, _ = fx_to_base(session, inst.currency, settings.base_currency, as_of)
        value = float(frame["close"].iloc[-1]) * pos.quantity * rate
        values[pos.instrument_id] = values.get(pos.instrument_id, 0.0) + value
        sectors[inst.sector] = sectors.get(inst.sector, 0.0) + value
    total = sum(values.values())
    if total <= 0:
        return {}, {}
    return (
        {k: v / total * 100 for k, v in values.items()},
        {k: v / total * 100 for k, v in sectors.items()},
    )


def persist_bundle(session: Session, run: ScoreRun, bundle: ScoreBundle) -> None:
    row = ScoreBundleRow(
        run_id=run.id,
        instrument_id=bundle.instrument_id,
        as_of=bundle.as_of,
        model_version=bundle.model_version,
        best_fit_horizon=bundle.best_fit_horizon,
        evidence=[e.model_dump(mode="json") for e in bundle.evidence],
        warnings=bundle.warnings,
    )
    session.add(row)
    session.flush()
    for horizon, h in bundle.horizons.items():
        session.add(
            ScoreRecord(
                run_id=run.id,
                bundle_id=row.id,
                instrument_id=bundle.instrument_id,
                as_of=bundle.as_of,
                horizon=horizon,
                opportunity=h.opportunity,
                confidence=h.confidence,
                risk=h.risk,
                components=h.components,
                abstained=h.abstained,
                abstain_reasons=h.abstain_reasons,
                gate=h.gate.model_dump(mode="json") if h.gate else None,
                explanation=h.explanation,
            )
        )
    for name, result in bundle.engine_results.items():
        session.add(
            EngineOutput(
                run_id=run.id,
                instrument_id=bundle.instrument_id,
                engine=name,
                score=result.score,
                components=result.components,
                evidence=[e.model_dump(mode="json") for e in result.evidence],
                warnings=result.warnings,
                data_quality=result.data_quality,
                details=_jsonable(result.details),
            )
        )


def _jsonable(obj):
    import json

    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return json.loads(json.dumps(obj, default=str))


def run_scan(
    session: Session,
    as_of: date,
    trigger: str = "manual",
    settings: Settings | None = None,
    deliver_alerts: bool = True,
) -> ScoreRun:
    settings = settings or get_settings()
    ensure_model_version(session, settings.scoring_model_version)

    run = ScoreRun(
        as_of=as_of,
        model_version=settings.scoring_model_version,
        config_hash=config_hash(settings.scoring_model_version),
        trigger=trigger,
    )
    session.add(run)
    session.flush()

    instruments = session.execute(
        select(Instrument).where(Instrument.security_type == "common")
    ).scalars().all()
    run.universe_size = len(instruments)

    weights_by_instr, weights_by_sector = _portfolio_weights(session, as_of, settings)
    owned = set(weights_by_instr)

    scored = abstained = alerts_out = 0
    skip_detail: dict[str, list[str]] = {}
    for inst in instruments:
        try:
            snapshot = build_snapshot(session, inst.id, as_of, settings)
        except SnapshotBuildError as exc:
            skip_detail.setdefault("no_data", []).append(f"{inst.ticker}: {exc.reason}")
            continue

        ok, reasons = universe_eligible(snapshot, settings)
        if not ok and inst.id not in owned:
            # Delisted names keep their history for backtests but are not
            # scanned live; owned-but-ineligible names still get risk review.
            skip_detail.setdefault("ineligible", []).append(
                f"{inst.ticker}: {'; '.join(reasons)}"
            )
            continue

        results = run_all_engines(snapshot, settings)
        bundle = score_instrument(snapshot, results, settings, as_of)
        persist_bundle(session, run, bundle)
        scored += 1
        if all(h.abstained for h in bundle.horizons.values()):
            abstained += 1

        candidates = generate_candidates(snapshot, bundle, settings) if ok else []
        if inst.id in owned:
            stance = portfolio_rules(
                snapshot, bundle, settings,
                weights_by_instr.get(inst.id), weights_by_sector.get(inst.sector),
            )
            if stance is not None:
                candidates.append(stance)

        drafts = sync_signals(session, run.id, snapshot, bundle, candidates, settings)
        for draft in drafts:
            alert = build_alert(session, run.id, snapshot, bundle, draft, settings)
            if deliver_alerts:
                deliver(session, alert, settings)
            alerts_out += 1

    run.scored = scored
    run.abstained = abstained
    run.status = "ok"
    run.detail = {
        "alerts": alerts_out,
        "skipped": {k: v[:20] for k, v in skip_detail.items()},
        "trigger": trigger,
    }
    session.add(
        AuditLog(
            action="scan_completed",
            detail={
                "run_id": run.id, "as_of": as_of.isoformat(), "scored": scored,
                "alerts": alerts_out, "model_version": settings.scoring_model_version,
            },
        )
    )
    run.run_at = datetime.now(UTC).replace(tzinfo=None)
    session.flush()
    log.info(
        "scan %s complete: %d scored, %d fully abstained, %d alerts",
        as_of, scored, abstained, alerts_out,
    )
    return run


def expire_stale_watches(session: Session, as_of: date) -> int:
    """Housekeeping used by scheduled scans: deactivate expired WATCHING
    signals that no scan touched (e.g. instrument dropped from universe)."""
    stale = session.execute(
        select(Signal).where(
            Signal.active.is_(True),
            Signal.state == "WATCHING",
            Signal.expires_at.is_not(None),
            Signal.expires_at < as_of,
        )
    ).scalars().all()
    for sig in stale:
        hist = list(sig.state_history or [])
        hist.append(
            {"state": "EXPIRED", "as_of": as_of.isoformat(), "reason": "expired unscanned"}
        )
        sig.state_history = hist
        sig.state = "EXPIRED"
        sig.active = False
    return len(stale)
