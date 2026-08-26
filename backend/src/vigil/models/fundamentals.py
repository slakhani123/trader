"""Point-in-time fundamentals, estimates and analyst targets.

``FundamentalReport`` rows are append-only observations: a restatement is a
NEW row with ``is_restatement=True`` and its own ``published_at``; snapshot
building decides what was visible when.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from vigil.db import Base
from vigil.models.reference import utcnow


class FundamentalReport(Base):
    __tablename__ = "fundamental_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    period_end: Mapped[date] = mapped_column(Date)
    period_type: Mapped[str] = mapped_column(String(2))  # Q | A
    published_at: Mapped[datetime] = mapped_column(DateTime)
    is_restatement: Mapped[bool] = mapped_column(Boolean, default=False)
    restates_period_end: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(8))
    # Normalised statement payload — keys mirror schemas.core.FundamentalRecord
    payload: Mapped[dict] = mapped_column(JSON)
    provider: Mapped[str] = mapped_column(String(32), default="")
    source_reference: Mapped[str] = mapped_column(String(300), default="")
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payloads.id"))
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_fund_pit", "instrument_id", "published_at"),)


class EstimateSnap(Base):
    """Consensus estimate observed on ``as_of`` (append-only snapshots)."""

    __tablename__ = "estimate_snaps"

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    as_of: Mapped[date] = mapped_column(Date)
    metric: Mapped[str] = mapped_column(String(12))  # eps | revenue
    fiscal_label: Mapped[str] = mapped_column(String(16))
    period_end: Mapped[date] = mapped_column(Date)
    mean: Mapped[float] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    analyst_count: Mapped[int] = mapped_column(Integer, default=0)
    mean_30d_ago: Mapped[float | None] = mapped_column(Float)
    mean_90d_ago: Mapped[float | None] = mapped_column(Float)
    up_revisions_30d: Mapped[int] = mapped_column(Integer, default=0)
    down_revisions_30d: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String(32), default="")

    __table_args__ = (
        Index("ix_estimates_pit", "instrument_id", "as_of", "metric", "fiscal_label"),
    )


class TargetSnap(Base):
    """Consensus price-target observed on ``as_of`` (append-only snapshots)."""

    __tablename__ = "target_snaps"

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    as_of: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(8))
    mean: Mapped[float] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    std: Mapped[float | None] = mapped_column(Float)
    analyst_count: Mapped[int] = mapped_column(Integer, default=0)
    median_age_days: Mapped[float | None] = mapped_column(Float)
    mean_30d_ago: Mapped[float | None] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String(32), default="")

    __table_args__ = (Index("ix_targets_pit", "instrument_id", "as_of"),)
