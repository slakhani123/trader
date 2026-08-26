"""Score runs, per-horizon scores, engine outputs, signals, immutable alerts."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from vigil.db import Base
from vigil.models.reference import utcnow


class ScoreRun(Base):
    """One scan over the universe. Records the exact model/config used."""

    __tablename__ = "score_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    model_version: Mapped[str] = mapped_column(String(24))
    config_hash: Mapped[str] = mapped_column(String(64))
    trigger: Mapped[str] = mapped_column(String(24), default="manual")  # manual|eod|intraday|backtest
    universe_size: Mapped[int] = mapped_column(Integer, default=0)
    scored: Mapped[int] = mapped_column(Integer, default=0)
    abstained: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="running")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class ScoreBundleRow(Base):
    """Instrument-level result of a run: best-fit horizon + shared evidence."""

    __tablename__ = "score_bundles"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("score_runs.id"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    as_of: Mapped[date] = mapped_column(Date)
    model_version: Mapped[str] = mapped_column(String(24))
    best_fit_horizon: Mapped[str | None] = mapped_column(String(8))
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)

    __table_args__ = (Index("ix_bundle_run_instr", "run_id", "instrument_id", unique=True),)


class ScoreRecord(Base):
    """One horizon's scores for one instrument in one run. Append-only."""

    __tablename__ = "score_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("score_runs.id"), index=True)
    bundle_id: Mapped[int] = mapped_column(ForeignKey("score_bundles.id"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    horizon: Mapped[str] = mapped_column(String(8))
    opportunity: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    risk: Mapped[float] = mapped_column(Float)
    components: Mapped[dict] = mapped_column(JSON, default=dict)
    abstained: Mapped[bool] = mapped_column(Boolean, default=False)
    abstain_reasons: Mapped[list] = mapped_column(JSON, default=list)
    gate: Mapped[dict | None] = mapped_column(JSON)
    explanation: Mapped[list] = mapped_column(JSON, default=list)

    __table_args__ = (
        Index("ix_score_run_instr_h", "run_id", "instrument_id", "horizon", unique=True),
    )


class EngineOutput(Base):
    """Full engine result (score, components, evidence, warnings) per run —
    this is what makes 'why did the score change' answerable."""

    __tablename__ = "engine_outputs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("score_runs.id"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    engine: Mapped[str] = mapped_column(String(24))
    score: Mapped[float | None] = mapped_column(Float)
    components: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    data_quality: Mapped[float] = mapped_column(Float, default=1.0)
    details: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (
        Index("ix_engine_run_instr", "run_id", "instrument_id", "engine", unique=True),
    )


class Signal(Base):
    """A live thesis for (instrument, family, horizon) moving through the
    lifecycle FSM. History is embedded append-only in ``state_history``."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    family: Mapped[str] = mapped_column(String(32), index=True)
    horizon: Mapped[str] = mapped_column(String(8))
    state: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    first_run_id: Mapped[int] = mapped_column(ForeignKey("score_runs.id"))
    last_run_id: Mapped[int] = mapped_column(ForeignKey("score_runs.id"))
    anchor_price: Mapped[float | None] = mapped_column(Float)  # price at trigger
    anchor_date: Mapped[date | None] = mapped_column(Date)
    entry_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    last_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    last_alert_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_alert_opportunity: Mapped[float | None] = mapped_column(Float)
    last_alert_risk: Mapped[float | None] = mapped_column(Float)
    last_alert_price: Mapped[float | None] = mapped_column(Float)
    state_history: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    __table_args__ = (
        Index("ix_signal_identity", "instrument_id", "family", "horizon", "active"),
    )


def _uuid() -> str:
    return uuid.uuid4().hex


class Alert(Base):
    """Immutable research alert. Rows are never updated after creation except
    the delivery/read bookkeeping flags — this is the shadow/paper record."""

    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("score_runs.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    as_of: Mapped[date] = mapped_column(Date)
    family: Mapped[str] = mapped_column(String(32))
    lifecycle_state: Mapped[str] = mapped_column(String(16))
    transition: Mapped[str] = mapped_column(String(48), default="")  # e.g. WATCHING->TRIGGERED
    horizon: Mapped[str] = mapped_column(String(8))
    priority: Mapped[str] = mapped_column(String(8), default="normal")  # high|normal|digest
    title: Mapped[str] = mapped_column(String(240))
    payload: Mapped[dict] = mapped_column(JSON)  # full AlertPayload (schemas/alerts.py)
    narrative_source: Mapped[str] = mapped_column(String(16), default="template")  # template|llm
    # bookkeeping (the only mutable fields)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    delivered: Mapped[dict] = mapped_column(JSON, default=dict)


class AlertNote(Base):
    """Optional user notes attached to alerts — kept separate so Alert stays immutable."""

    __tablename__ = "alert_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    body: Mapped[str] = mapped_column(Text)
