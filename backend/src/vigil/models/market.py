"""Market data: raw (unadjusted) daily bars, corporate actions, FX, macro.

Bars are stored UNADJUSTED. Adjustment is computed at snapshot-build time
from corporate actions with ``ex_date <= as_of`` only, which keeps history
point-in-time correct (a split announced later never rewrites what a
backtest saw earlier).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from vigil.db import Base
from vigil.models.reference import utcnow


class PriceBar(Base):
    __tablename__ = "price_bars"

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    bar_date: Mapped[date] = mapped_column(Date)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(8))
    provider: Mapped[str] = mapped_column(String(32), default="")
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_price_bars_instrument_date", "instrument_id", "bar_date", unique=True),
    )


class CorporateAction(Base):
    __tablename__ = "corporate_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # split|dividend|ticker_change|acquisition|delisting
    ex_date: Mapped[date] = mapped_column(Date, index=True)
    factor: Mapped[float | None] = mapped_column(Float)  # split ratio, e.g. 4.0 for 4:1
    amount: Mapped[float | None] = mapped_column(Float)  # dividend per share, local ccy
    detail: Mapped[str] = mapped_column(String(240), default="")
    provider: Mapped[str] = mapped_column(String(32), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FxRate(Base):
    """Daily FX close, quoted as 1 unit of ``base_ccy`` in ``quote_ccy``.
    e.g. base=USD quote=GBP rate=0.79 means 1 USD = 0.79 GBP."""

    __tablename__ = "fx_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    base_ccy: Mapped[str] = mapped_column(String(8))
    quote_ccy: Mapped[str] = mapped_column(String(8))
    rate_date: Mapped[date] = mapped_column(Date)
    rate: Mapped[float] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String(32), default="")

    __table_args__ = (
        Index("ix_fx_pair_date", "base_ccy", "quote_ccy", "rate_date", unique=True),
    )


class MacroObservation(Base):
    """Macro series observations with explicit publication timestamps.

    ``series_id`` examples: us_policy_rate, uk_policy_rate, us_cpi_yoy,
    uk_cpi_yoy, us_credit_spread_bps, vix, us_10y_yield, uk_10y_yield.
    ``published_at`` matters: CPI for month M is only visible after release.
    """

    __tablename__ = "macro_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[str] = mapped_column(String(48), index=True)
    obs_date: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Float)
    published_at: Mapped[datetime] = mapped_column(DateTime)
    provider: Mapped[str] = mapped_column(String(32), default="")

    __table_args__ = (
        Index("ix_macro_series_obs", "series_id", "obs_date", unique=True),
        Index("ix_macro_series_pub", "series_id", "published_at"),
    )
