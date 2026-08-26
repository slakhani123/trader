"""News, catalysts, short interest, insider transactions."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from vigil.db import Base
from vigil.models.reference import utcnow


class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(120), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    headline: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    source_name: Mapped[str] = mapped_column(String(80))
    # factual_event | management_claim | analyst_opinion | market_commentary | social
    source_type: Mapped[str] = mapped_column(String(24))
    url: Mapped[str] = mapped_column(String(400), default="")
    sentiment: Mapped[float] = mapped_column(Float, default=0.0)  # deterministic, -1..1
    novelty: Mapped[float] = mapped_column(Float, default=1.0)  # 0..1, 1 = new information
    duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("news_items.id"))
    provider: Mapped[str] = mapped_column(String(32), default="")
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_news_dedup", "instrument_id", "external_id", unique=True),
    )


class Catalyst(Base):
    __tablename__ = "catalysts"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(120))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # see schemas.core.CatalystKind
    expected_date: Mapped[date] = mapped_column(Date, index=True)
    date_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(String(400))
    binary: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome: Mapped[str | None] = mapped_column(String(400))
    outcome_date: Mapped[date | None] = mapped_column(Date)
    url: Mapped[str] = mapped_column(String(400), default="")
    provider: Mapped[str] = mapped_column(String(32), default="")
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_catalyst_dedup", "instrument_id", "external_id", unique=True),
    )


class ShortInterestObs(Base):
    __tablename__ = "short_interest_obs"

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    as_of: Mapped[date] = mapped_column(Date)
    published_at: Mapped[datetime] = mapped_column(DateTime)
    shares_short: Mapped[float] = mapped_column(Float)
    pct_float: Mapped[float | None] = mapped_column(Float)
    days_to_cover: Mapped[float | None] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String(32), default="")

    __table_args__ = (Index("ix_short_pit", "instrument_id", "published_at"),)


class InsiderTx(Base):
    __tablename__ = "insider_txs"

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    filed_at: Mapped[datetime] = mapped_column(DateTime)
    transaction_date: Mapped[date] = mapped_column(Date)
    insider_name: Mapped[str] = mapped_column(String(120))
    insider_role: Mapped[str] = mapped_column(String(80), default="")
    kind: Mapped[str] = mapped_column(String(8))  # buy | sell
    shares: Mapped[float] = mapped_column(Float)
    value: Mapped[float | None] = mapped_column(Float)
    url: Mapped[str] = mapped_column(String(400), default="")
    provider: Mapped[str] = mapped_column(String(32), default="")

    __table_args__ = (Index("ix_insider_pit", "instrument_id", "filed_at"),)
