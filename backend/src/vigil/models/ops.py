"""Operational tables: raw payload lineage, provider health, jobs, audit,
model versions, notification deliveries."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from vigil.db import Base
from vigil.models.reference import utcnow


class RawPayload(Base):
    """Raw provider response, stored before normalisation (data lineage)."""

    __tablename__ = "raw_payloads"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    endpoint: Mapped[str] = mapped_column(String(200))
    params_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)  # JSON/text as received
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_raw_provider_hash", "provider", "params_hash"),)


class ProviderHealthRecord(Base):
    __tablename__ = "provider_health"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    capability: Mapped[str] = mapped_column(String(24))  # prices|fundamentals|news|...
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    configured: Mapped[bool] = mapped_column(Boolean, default=True)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    message: Mapped[str] = mapped_column(String(400), default="")
    staleness_days: Mapped[float | None] = mapped_column(Float)


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_name: Mapped[str] = mapped_column(String(48), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|ok|failed
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(48), default="system")
    action: Mapped[str] = mapped_column(String(96))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class ModelVersion(Base):
    """Versioned scoring configuration: weights, gates, formula hash.
    Every ScoreRun references one; rows are never mutated once used."""

    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(24), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    weights: Mapped[dict] = mapped_column(JSON)
    config_hash: Mapped[str] = mapped_column(String(64))
    formula_hash: Mapped[str] = mapped_column(String(64), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=False)


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[str | None] = mapped_column(ForeignKey("alerts.id"), index=True)
    channel: Mapped[str] = mapped_column(String(16))  # inapp|webhook|email|push
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|sent|failed|skipped
    detail: Mapped[str] = mapped_column(String(400), default="")
