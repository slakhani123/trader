"""Provider payload -> normalised point-in-time store.

Responsibilities: raw-payload lineage, schema validation, deduplication,
idempotent upserts, and ingest statistics for job journaling. Append-only
tables (fundamentals, estimate/target snapshots, news, observations) are
never updated in place — a changed fact is a new observation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from vigil.models import (
    Catalyst,
    CorporateAction,
    EstimateSnap,
    FundamentalReport,
    FxRate,
    InsiderTx,
    Instrument,
    MacroObservation,
    NewsItem,
    PriceBar,
    RawPayload,
    SharesOutstandingObs,
    ShortInterestObs,
    TargetSnap,
    TickerChange,
)
from vigil.providers import base as p


@dataclass
class IngestStats:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    rejected: int = 0
    issues: list[str] = field(default_factory=list)

    def merge(self, other: IngestStats) -> None:
        self.inserted += other.inserted
        self.updated += other.updated
        self.skipped += other.skipped
        self.rejected += other.rejected
        self.issues.extend(other.issues)

    def as_dict(self) -> dict:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped": self.skipped,
            "rejected": self.rejected,
            "issues": self.issues[:50],
        }


def store_raw(
    session: Session, provider: str, endpoint: str, params: str, payload: str
) -> int | None:
    """Store the raw provider response for lineage. Returns row id."""
    if not payload:
        return None
    row = RawPayload(
        provider=provider,
        endpoint=endpoint[:200],
        params_hash=hashlib.sha256(params.encode()).hexdigest(),
        payload=payload,
    )
    session.add(row)
    session.flush()
    return row.id


def ensure_instruments(
    session: Session, payloads: list[p.InstrumentPayload], provider: str
) -> dict[str, Instrument]:
    """Upsert instruments by (ticker, exchange). Delisting info updates in
    place (it is reference state, not a PIT observation — the corporate
    action row carries the dated event)."""
    out: dict[str, Instrument] = {}
    for pl in payloads:
        row = session.execute(
            select(Instrument).where(
                Instrument.ticker == pl.ticker, Instrument.exchange == pl.exchange
            )
        ).scalar_one_or_none()
        if row is None:
            row = Instrument(
                ticker=pl.ticker,
                exchange=pl.exchange,
                market=pl.market,
                name=pl.name,
                sector=pl.sector,
                industry=pl.industry,
                currency=pl.currency,
                security_type=pl.security_type,
                is_shell=pl.is_shell,
                listed_at=pl.listed_at,
                delisted_at=pl.delisted_at,
                delisting_reason=pl.delisting_reason,
                is_active=pl.delisted_at is None,
            )
            session.add(row)
            session.flush()
        else:
            row.name = pl.name or row.name
            row.sector = pl.sector or row.sector
            row.industry = pl.industry or row.industry
            if pl.delisted_at is not None:
                row.delisted_at = pl.delisted_at
                row.delisting_reason = pl.delisting_reason
                row.is_active = False
        out[pl.ticker] = row
    return out


def _valid_bar(pl: p.BarPayload) -> str | None:
    if pl.close <= 0 or pl.open <= 0:
        return f"{pl.ticker} {pl.bar_date}: non-positive price"
    if pl.high < pl.low:
        return f"{pl.ticker} {pl.bar_date}: high < low"
    if not (pl.low <= pl.open <= pl.high) or not (pl.low <= pl.close <= pl.high):
        return f"{pl.ticker} {pl.bar_date}: OHLC out of range"
    if pl.volume < 0:
        return f"{pl.ticker} {pl.bar_date}: negative volume"
    return None


def ingest_bars(
    session: Session, instrument: Instrument, payloads: list[p.BarPayload], provider: str
) -> IngestStats:
    stats = IngestStats()
    if not payloads:
        return stats
    existing = {
        d
        for (d,) in session.execute(
            select(PriceBar.bar_date).where(PriceBar.instrument_id == instrument.id)
        )
    }
    for pl in payloads:
        err = _valid_bar(pl)
        if err:
            stats.rejected += 1
            stats.issues.append(err)
            continue
        if pl.bar_date in existing:
            stats.skipped += 1
            continue
        session.add(
            PriceBar(
                instrument_id=instrument.id,
                bar_date=pl.bar_date,
                open=pl.open,
                high=pl.high,
                low=pl.low,
                close=pl.close,
                volume=pl.volume,
                currency=pl.currency,
                provider=provider,
            )
        )
        existing.add(pl.bar_date)
        stats.inserted += 1
    return stats


def ingest_actions(
    session: Session, instrument: Instrument, payloads: list[p.ActionPayload], provider: str
) -> IngestStats:
    stats = IngestStats()
    existing = {
        (k, d)
        for (k, d) in session.execute(
            select(CorporateAction.kind, CorporateAction.ex_date).where(
                CorporateAction.instrument_id == instrument.id
            )
        )
    }
    for pl in payloads:
        if pl.kind == "split" and (pl.factor is None or pl.factor <= 0):
            stats.rejected += 1
            stats.issues.append(f"{pl.ticker} split without valid factor on {pl.ex_date}")
            continue
        if (pl.kind, pl.ex_date) in existing:
            stats.skipped += 1
            continue
        session.add(
            CorporateAction(
                instrument_id=instrument.id,
                kind=pl.kind,
                ex_date=pl.ex_date,
                factor=pl.factor,
                amount=pl.amount,
                detail=pl.detail,
                provider=provider,
                published_at=pl.published_at,
            )
        )
        if pl.kind == "ticker_change" and "->" in pl.detail:
            old, new = pl.detail.split("->", 1)
            session.add(
                TickerChange(
                    instrument_id=instrument.id,
                    old_ticker=old.strip(),
                    new_ticker=new.strip(),
                    effective_date=pl.ex_date,
                    provider=provider,
                )
            )
        stats.inserted += 1
    return stats


def ingest_fundamentals(
    session: Session,
    instrument: Instrument,
    payloads: list[p.FundamentalPayload],
    provider: str,
    raw_id: int | None = None,
) -> IngestStats:
    stats = IngestStats()
    existing = {
        (pe, pt, pub)
        for (pe, pt, pub) in session.execute(
            select(
                FundamentalReport.period_end,
                FundamentalReport.period_type,
                FundamentalReport.published_at,
            ).where(FundamentalReport.instrument_id == instrument.id)
        )
    }
    for pl in payloads:
        if pl.period_type not in ("Q", "A"):
            stats.rejected += 1
            stats.issues.append(f"{pl.ticker}: bad period_type {pl.period_type}")
            continue
        pub = _naive_utc(pl.published_at)
        if pub.date() < pl.period_end:
            stats.rejected += 1
            stats.issues.append(
                f"{pl.ticker} {pl.period_end}: published before period end — refused "
                "(would inject look-ahead)"
            )
            continue
        if (pl.period_end, pl.period_type, pub) in existing:
            stats.skipped += 1
            continue
        session.add(
            FundamentalReport(
                instrument_id=instrument.id,
                period_end=pl.period_end,
                period_type=pl.period_type,
                published_at=pub,
                is_restatement=pl.is_restatement,
                restates_period_end=pl.restates_period_end,
                currency=pl.currency,
                payload={k: v for k, v in pl.fields.items() if v is not None},
                provider=provider,
                source_reference=pl.source_reference,
                raw_payload_id=raw_id,
            )
        )
        if pl.shares_outstanding:
            session.add(
                SharesOutstandingObs(
                    instrument_id=instrument.id,
                    as_of=pl.period_end,
                    published_at=pub,
                    shares=pl.shares_outstanding,
                    provider=provider,
                )
            )
        stats.inserted += 1
    return stats


def ingest_estimates(
    session: Session, instrument: Instrument, payloads: list[p.EstimatePayload], provider: str
) -> IngestStats:
    stats = IngestStats()
    existing = {
        key
        for key in session.execute(
            select(
                EstimateSnap.as_of, EstimateSnap.metric, EstimateSnap.fiscal_label
            ).where(EstimateSnap.instrument_id == instrument.id)
        )
    }
    for pl in payloads:
        if (pl.as_of, pl.metric, pl.fiscal_label) in existing:
            stats.skipped += 1
            continue
        session.add(
            EstimateSnap(
                instrument_id=instrument.id,
                as_of=pl.as_of,
                metric=pl.metric,
                fiscal_label=pl.fiscal_label,
                period_end=pl.period_end,
                mean=pl.mean,
                high=pl.high,
                low=pl.low,
                analyst_count=pl.analyst_count,
                mean_30d_ago=pl.mean_30d_ago,
                mean_90d_ago=pl.mean_90d_ago,
                up_revisions_30d=pl.up_revisions_30d,
                down_revisions_30d=pl.down_revisions_30d,
                provider=provider,
            )
        )
        stats.inserted += 1
    return stats


def ingest_targets(
    session: Session, instrument: Instrument, payloads: list[p.TargetPayload], provider: str
) -> IngestStats:
    stats = IngestStats()
    existing = {
        d
        for (d,) in session.execute(
            select(TargetSnap.as_of).where(TargetSnap.instrument_id == instrument.id)
        )
    }
    for pl in payloads:
        if pl.as_of in existing:
            stats.skipped += 1
            continue
        session.add(
            TargetSnap(
                instrument_id=instrument.id,
                as_of=pl.as_of,
                currency=pl.currency,
                mean=pl.mean,
                high=pl.high,
                low=pl.low,
                std=pl.std,
                analyst_count=pl.analyst_count,
                median_age_days=pl.median_age_days,
                mean_30d_ago=pl.mean_30d_ago,
                provider=provider,
            )
        )
        stats.inserted += 1
    return stats


def ingest_news(
    session: Session, instrument: Instrument, payloads: list[p.NewsPayload], provider: str
) -> IngestStats:
    stats = IngestStats()
    existing = {
        e
        for (e,) in session.execute(
            select(NewsItem.external_id).where(NewsItem.instrument_id == instrument.id)
        )
    }
    seen_headlines = {
        h
        for (h,) in session.execute(
            select(NewsItem.headline).where(NewsItem.instrument_id == instrument.id)
        )
    }
    for pl in payloads:
        if pl.external_id in existing:
            stats.skipped += 1
            continue
        novelty = pl.novelty
        if pl.headline in seen_headlines:
            novelty = 0.0  # duplicate content from another source
        session.add(
            NewsItem(
                external_id=pl.external_id,
                instrument_id=instrument.id,
                published_at=_naive_utc(pl.published_at),
                headline=pl.headline[:300],
                summary=pl.summary,
                source_name=pl.source_name,
                source_type=pl.source_type,
                url=pl.url,
                sentiment=max(-1.0, min(1.0, pl.sentiment)),
                novelty=novelty,
                provider=provider,
            )
        )
        existing.add(pl.external_id)
        seen_headlines.add(pl.headline)
        stats.inserted += 1
    return stats


def ingest_catalysts(
    session: Session, instrument: Instrument, payloads: list[p.CatalystPayload], provider: str
) -> IngestStats:
    stats = IngestStats()
    rows = {
        row.external_id: row
        for row in session.execute(
            select(Catalyst).where(Catalyst.instrument_id == instrument.id)
        ).scalars()
    }
    for pl in payloads:
        row = rows.get(pl.external_id)
        if row is None:
            session.add(
                Catalyst(
                    external_id=pl.external_id,
                    instrument_id=instrument.id,
                    kind=pl.kind,
                    expected_date=pl.expected_date,
                    date_confirmed=pl.date_confirmed,
                    description=pl.description[:400],
                    binary=pl.binary,
                    published_at=_naive_utc(pl.published_at) if pl.published_at else None,
                    resolved=pl.resolved,
                    outcome=pl.outcome,
                    outcome_date=pl.outcome_date,
                    url=pl.url,
                    provider=provider,
                )
            )
            stats.inserted += 1
        else:
            # Resolution/date updates are dated via outcome_date, so the
            # snapshot builder can still present the pre-resolution view.
            row.expected_date = pl.expected_date
            row.date_confirmed = pl.date_confirmed
            if pl.resolved and not row.resolved:
                row.resolved = True
                row.outcome = pl.outcome
                row.outcome_date = pl.outcome_date
            stats.updated += 1
    return stats


def ingest_short_interest(
    session: Session, instrument: Instrument, payloads: list[p.ShortInterestPayload], provider: str
) -> IngestStats:
    stats = IngestStats()
    existing = {
        d
        for (d,) in session.execute(
            select(ShortInterestObs.as_of).where(ShortInterestObs.instrument_id == instrument.id)
        )
    }
    for pl in payloads:
        if pl.as_of in existing:
            stats.skipped += 1
            continue
        session.add(
            ShortInterestObs(
                instrument_id=instrument.id,
                as_of=pl.as_of,
                published_at=_naive_utc(pl.published_at),
                shares_short=pl.shares_short,
                pct_float=pl.pct_float,
                days_to_cover=pl.days_to_cover,
                provider=provider,
            )
        )
        stats.inserted += 1
    return stats


def ingest_insiders(
    session: Session, instrument: Instrument, payloads: list[p.InsiderPayload], provider: str
) -> IngestStats:
    stats = IngestStats()
    existing = {
        (f, n, d)
        for (f, n, d) in session.execute(
            select(
                InsiderTx.filed_at, InsiderTx.insider_name, InsiderTx.transaction_date
            ).where(InsiderTx.instrument_id == instrument.id)
        )
    }
    for pl in payloads:
        key = (_naive_utc(pl.filed_at), pl.insider_name, pl.transaction_date)
        if key in existing:
            stats.skipped += 1
            continue
        session.add(
            InsiderTx(
                instrument_id=instrument.id,
                filed_at=_naive_utc(pl.filed_at),
                transaction_date=pl.transaction_date,
                insider_name=pl.insider_name,
                insider_role=pl.insider_role,
                kind=pl.kind,
                shares=pl.shares,
                value=pl.value,
                url=pl.url,
                provider=provider,
            )
        )
        stats.inserted += 1
    return stats


def ingest_macro(
    session: Session, payloads: list[p.MacroPayload], provider: str
) -> IngestStats:
    stats = IngestStats()
    existing = {
        (s, d)
        for (s, d) in session.execute(
            select(MacroObservation.series_id, MacroObservation.obs_date)
        )
    }
    for pl in payloads:
        if (pl.series_id, pl.obs_date) in existing:
            stats.skipped += 1
            continue
        session.add(
            MacroObservation(
                series_id=pl.series_id,
                obs_date=pl.obs_date,
                value=pl.value,
                published_at=_naive_utc(pl.published_at),
                provider=provider,
            )
        )
        stats.inserted += 1
    return stats


def ingest_fx(session: Session, payloads: list[p.FxPayload], provider: str) -> IngestStats:
    stats = IngestStats()
    existing = {
        (b, q, d)
        for (b, q, d) in session.execute(
            select(FxRate.base_ccy, FxRate.quote_ccy, FxRate.rate_date)
        )
    }
    for pl in payloads:
        if pl.rate <= 0:
            stats.rejected += 1
            stats.issues.append(f"FX {pl.base_ccy}{pl.quote_ccy} {pl.rate_date}: rate <= 0")
            continue
        if (pl.base_ccy, pl.quote_ccy, pl.rate_date) in existing:
            stats.skipped += 1
            continue
        session.add(
            FxRate(
                base_ccy=pl.base_ccy,
                quote_ccy=pl.quote_ccy,
                rate_date=pl.rate_date,
                rate=pl.rate,
                provider=provider,
            )
        )
        stats.inserted += 1
    return stats


def _naive_utc(dt: datetime) -> datetime:
    """Normalise to naive UTC for storage (SQLite has no tz support)."""
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def last_bar_date(session: Session, instrument_id: int) -> date | None:
    from sqlalchemy import func

    return session.execute(
        select(func.max(PriceBar.bar_date)).where(PriceBar.instrument_id == instrument_id)
    ).scalar()
