"""Backtest runs and simulated lifecycle trades."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from vigil.db import Base
from vigil.models.reference import utcnow


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    name: Mapped[str] = mapped_column(String(120))
    model_version: Mapped[str] = mapped_column(String(24))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    holdout_start: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="running")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    by_bucket: Mapped[dict] = mapped_column(JSON, default=dict)
    calibration: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    family: Mapped[str] = mapped_column(String(32))
    horizon: Mapped[str] = mapped_column(String(8))
    signal_date: Mapped[date] = mapped_column(Date)
    entry_date: Mapped[date | None] = mapped_column(Date)
    entry_price: Mapped[float | None] = mapped_column(Float)
    exit_date: Mapped[date | None] = mapped_column(Date)
    exit_price: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str] = mapped_column(String(48), default="")
    holding_days: Mapped[int | None] = mapped_column(Integer)
    return_pct: Mapped[float | None] = mapped_column(Float)
    benchmark_return_pct: Mapped[float | None] = mapped_column(Float)
    mae_pct: Mapped[float | None] = mapped_column(Float)  # max adverse excursion
    mfe_pct: Mapped[float | None] = mapped_column(Float)  # max favourable excursion
    costs_bps: Mapped[float | None] = mapped_column(Float)
    opportunity: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    risk: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (Index("ix_bt_trade_run_instr", "run_id", "instrument_id"),)
