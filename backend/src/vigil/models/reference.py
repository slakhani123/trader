"""Reference data: instruments, identifier history, point-in-time share counts."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from vigil.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(24), index=True)
    exchange: Mapped[str] = mapped_column(String(16))
    market: Mapped[str] = mapped_column(String(8), index=True)  # US | UK | INDEX | SECTOR
    name: Mapped[str] = mapped_column(String(160))
    sector: Mapped[str] = mapped_column(String(64), default="", index=True)
    industry: Mapped[str] = mapped_column(String(96), default="")
    currency: Mapped[str] = mapped_column(String(8))
    security_type: Mapped[str] = mapped_column(String(16), default="common")
    is_shell: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    listed_at: Mapped[date | None] = mapped_column(Date)
    delisted_at: Mapped[date | None] = mapped_column(Date)
    delisting_reason: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_instruments_ticker_exchange", "ticker", "exchange", unique=True),)


class TickerChange(Base):
    __tablename__ = "ticker_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    old_ticker: Mapped[str] = mapped_column(String(24))
    new_ticker: Mapped[str] = mapped_column(String(24))
    effective_date: Mapped[date] = mapped_column(Date)
    provider: Mapped[str] = mapped_column(String(32), default="")


class SharesOutstandingObs(Base):
    """Point-in-time share counts: rows are observations, never overwritten."""

    __tablename__ = "shares_outstanding_obs"

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    as_of: Mapped[date] = mapped_column(Date)
    published_at: Mapped[datetime] = mapped_column(DateTime)
    shares: Mapped[float] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String(32), default="")

    __table_args__ = (
        Index("ix_shares_obs_pit", "instrument_id", "published_at"),
    )
