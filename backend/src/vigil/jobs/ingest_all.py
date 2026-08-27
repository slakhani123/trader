"""Full ingest pass: pull every capability from the configured providers
into the point-in-time store. Idempotent — append-only tables dedupe.

Used both by the demo seed (synthetic provider) and by real refresh jobs.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from vigil.config import get_settings
from vigil.data import ingest
from vigil.models import Instrument, JobRun, ProviderHealthRecord
from vigil.providers.base import CapabilityUnavailable, ProviderError
from vigil.providers.registry import get_provider

log = logging.getLogger(__name__)

MACRO_SERIES = [
    "us_policy_rate", "uk_policy_rate", "us_cpi_yoy", "uk_cpi_yoy",
    "us_10y_yield", "uk_10y_yield", "us_credit_spread_bps", "vix",
]


def record_health(
    session: Session, provider: str, capability: str, ok: bool, message: str,
    configured: bool = True, latency_ms: float | None = None,
) -> None:
    session.add(
        ProviderHealthRecord(
            provider=provider, capability=capability, ok=ok, configured=configured,
            message=message[:400], latency_ms=latency_ms,
        )
    )


def _resolve(session: Session, capability: str):
    """Resolve a capability's provider; an unconfigured or unconstructable
    provider is recorded in Data Health and returns None instead of sinking
    the whole ingest (real-data setups legitimately leave capabilities off)."""
    try:
        return get_provider(capability)
    except CapabilityUnavailable as exc:
        record_health(session, "none", capability, False, str(exc), configured=False)
        return None


def ingest_universe(session: Session, start: date, end: date) -> dict:
    """Ingest reference + all per-instrument capabilities. Returns stats."""
    settings = get_settings()
    job = JobRun(job_name="ingest_all", detail={})
    session.add(job)
    session.flush()
    totals: dict[str, dict] = {}

    ref = _resolve(session, "reference")
    if ref is None:
        raise CapabilityUnavailable(
            "No universe source configured. Set VIGIL_PROVIDER_REFERENCE=static and "
            "create universe.yml (see universe.example.yml), or use the synthetic demo."
        )
    result = ref.fetch_universe(settings.universe.markets)
    for warning in result.warnings:
        record_health(session, ref.name, "reference", True, warning)
    instruments = ingest.ensure_instruments(session, result.records, ref.name)
    record_health(session, ref.name, "reference", True, f"{len(instruments)} instruments")

    prices = get_provider("prices")
    for ticker, inst in instruments.items():
        stats = ingest.IngestStats()
        try:
            bars = prices.fetch_bars(ticker, start, end)
            if bars.raw:
                ingest.store_raw(session, prices.name, bars.endpoint, ticker, bars.raw)
            stats.merge(ingest.ingest_bars(session, inst, bars.records, prices.name))
            actions = prices.fetch_actions(ticker, start, end)
            stats.merge(ingest.ingest_actions(session, inst, actions.records, prices.name))
        except CapabilityUnavailable as exc:
            record_health(session, prices.name, "prices", False, str(exc))
            bucket = totals.setdefault("prices", {"inserted": 0, "failed_tickers": 0})
            bucket["failed_tickers"] = bucket.get("failed_tickers", 0) + 1
        except ProviderError as exc:
            record_health(session, prices.name, "prices", False, str(exc))
            log.warning("price ingest failed for %s: %s", ticker, exc)
            bucket = totals.setdefault("prices", {"inserted": 0, "failed_tickers": 0})
            bucket["failed_tickers"] = bucket.get("failed_tickers", 0) + 1
        totals.setdefault("prices", {"inserted": 0})["inserted"] += stats.inserted
    bars_in = totals.get("prices", {}).get("inserted", 0)
    price_failures = totals.get("prices", {}).get("failed_tickers", 0)
    record_health(
        session, prices.name, "prices", bars_in > 0,
        f"{bars_in} bars ingested; {price_failures} ticker(s) failed"
        + ("" if bars_in else " — check network/provider (see per-ticker rows above)"),
    )

    fund = _resolve(session, "fundamentals")
    est = _resolve(session, "estimates")
    news = _resolve(session, "news")
    own = _resolve(session, "ownership")
    common = [i for i in instruments.values() if i.security_type == "common"]
    for inst in common:
        t = inst.ticker
        if fund is not None:
            try:
                fr = fund.fetch_fundamentals(t, start, end)
                raw_id = (
                    ingest.store_raw(session, fund.name, fr.endpoint, t, fr.raw)
                    if fr.raw else None
                )
                s = ingest.ingest_fundamentals(session, inst, fr.records, fund.name, raw_id)
                _acc(totals, "fundamentals", s)
            except (CapabilityUnavailable, ProviderError) as exc:
                record_health(session, fund.name, "fundamentals", False, f"{t}: {exc}")
        if est is not None:
            try:
                _acc(totals, "estimates", ingest.ingest_estimates(
                    session, inst, est.fetch_estimates(t, end).records, est.name))
                _acc(totals, "targets", ingest.ingest_targets(
                    session, inst, est.fetch_targets(t, end).records, est.name))
            except (CapabilityUnavailable, ProviderError) as exc:
                record_health(session, est.name, "estimates", False, f"{t}: {exc}")
        if news is not None:
            try:
                _acc(totals, "news", ingest.ingest_news(
                    session, inst, news.fetch_news(t, start, end).records, news.name))
                _acc(totals, "catalysts", ingest.ingest_catalysts(
                    session, inst, news.fetch_catalysts(t, end).records, news.name))
            except (CapabilityUnavailable, ProviderError) as exc:
                record_health(session, news.name, "news", False, f"{t}: {exc}")
        if own is not None:
            try:
                _acc(totals, "short_interest", ingest.ingest_short_interest(
                    session, inst, own.fetch_short_interest(t, start, end).records, own.name))
                _acc(totals, "insiders", ingest.ingest_insiders(
                    session, inst, own.fetch_insiders(t, start, end).records, own.name))
            except (CapabilityUnavailable, ProviderError) as exc:
                record_health(session, own.name, "ownership", False, f"{t}: {exc}")

    macro = _resolve(session, "macro")
    if macro is not None:
        try:
            _acc(totals, "macro", ingest.ingest_macro(
                session, macro.fetch_macro(MACRO_SERIES, start, end).records, macro.name))
            _acc(totals, "fx", ingest.ingest_fx(
                session, macro.fetch_fx([("USD", "GBP"), ("GBP", "USD")], start, end).records,
                macro.name))
            record_health(session, macro.name, "macro", True, "ok")
        except (CapabilityUnavailable, ProviderError) as exc:
            record_health(session, macro.name, "macro", False, str(exc))

    # Options capability: report configured/unavailable honestly.
    try:
        get_provider("options")
        record_health(session, settings.provider_options or "none", "options", True, "configured")
    except CapabilityUnavailable as exc:
        record_health(session, "none", "options", False, str(exc), configured=False)

    job.finished_at = datetime.now(UTC)
    job.status = "ok"
    job.detail = {k: v if isinstance(v, dict) else v.as_dict() for k, v in totals.items()}
    session.flush()
    return job.detail


def _acc(totals: dict, key: str, stats: ingest.IngestStats) -> None:
    agg = totals.setdefault(key, ingest.IngestStats())
    agg.merge(stats)


def universe_instruments(session: Session) -> list[Instrument]:
    from sqlalchemy import select

    return list(
        session.execute(
            select(Instrument).where(Instrument.security_type == "common")
        ).scalars()
    )
