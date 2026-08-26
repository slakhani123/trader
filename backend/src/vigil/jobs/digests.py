"""Digest and reminder notifications.

Three scheduled summaries, all deterministic given database contents:

* ``send_daily_digest``       — top gate-passing candidates from the latest
  ok scan (per horizon, capped at ``alert_policy.max_daily_new_alerts``)
  plus a count of alerts raised in the last 24 hours.
* ``send_weekly_review``      — active signals grouped by lifecycle state,
  WEAKENING/TRIM first, with price-vs-anchor change and thesis age.
* ``send_catalyst_reminders`` — unresolved catalysts due within N days for
  instruments that are owned, watchlisted, or carry an active signal.

Delivery mirrors ``alerts.notify``: an in-app NotificationDelivery row is
always recorded (detail is the digest text, truncated to 400 chars) and the
same content is POSTed to the webhook when configured, guarded so a webhook
failure never breaks the job. Each function returns its digest dict so tests
and the API can inspect exactly what was sent. ``now``/``as_of`` are
parameters with sensible defaults so tests can pin dates.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vigil.config import Settings
from vigil.models import (
    Alert,
    Catalyst,
    Instrument,
    NotificationDelivery,
    PortfolioPosition,
    PriceBar,
    ScoreRecord,
    ScoreRun,
    Signal,
    WatchlistItem,
)

log = logging.getLogger(__name__)

DISCLAIMER = "Research support only — not investment advice."

# Weekly review lists deteriorating theses first.
_STATE_ORDER = {"WEAKENING": 0, "TRIM": 1}


def _origin(settings: Settings) -> str:
    return settings.cors_origins[0] if settings.cors_origins else "http://localhost:5173"


def company_link(instrument_id: int, settings: Settings) -> str:
    return f"{_origin(settings)}/companies/{instrument_id}"


def _naive_utc(now: datetime | None) -> datetime:
    """Normalise to naive UTC to match stored timestamps."""
    if now is None:
        now = datetime.now(UTC)
    if now.tzinfo is not None:
        now = now.astimezone(UTC).replace(tzinfo=None)
    return now


def _deliver(session: Session, settings: Settings, kind: str, text: str, items: list[dict]) -> dict:
    """In-app row always; webhook POST when configured (never raises)."""
    delivered: dict[str, str] = {"inapp": "sent"}
    session.add(
        NotificationDelivery(alert_id=None, channel="inapp", status="sent", detail=text[:400])
    )
    if settings.webhook_url:
        try:
            resp = httpx.post(
                settings.webhook_url,
                json={"type": kind, "text": text, "items": items},
                timeout=10.0,
            )
            status = "sent" if resp.status_code < 300 else "failed"
            detail = f"HTTP {resp.status_code}"
        except Exception as exc:
            log.warning("%s webhook delivery failed: %s", kind, exc)
            status, detail = "failed", str(exc)
        delivered["webhook"] = status
        session.add(
            NotificationDelivery(
                alert_id=None, channel="webhook", status=status, detail=detail[:400]
            )
        )
    return delivered


def _latest_ok_run(session: Session, as_of: date | None) -> ScoreRun | None:
    stmt = select(ScoreRun).where(ScoreRun.status == "ok")
    if as_of is not None:
        stmt = stmt.where(ScoreRun.as_of == as_of)
    stmt = stmt.order_by(ScoreRun.as_of.desc(), ScoreRun.id.desc()).limit(1)
    return session.execute(stmt).scalars().first()


# --------------------------------------------------------------------------
# Daily digest
# --------------------------------------------------------------------------


def send_daily_digest(
    session: Session,
    settings: Settings,
    as_of: date | None = None,
    now: datetime | None = None,
) -> dict:
    """Top gate-passing candidates from the latest ok run + 24h alert count.

    ``as_of`` pins which run date to summarise (default: latest ok run);
    ``now`` pins the 24-hour alert window (default: current UTC time).
    """
    now = _naive_utc(now)
    run = _latest_ok_run(session, as_of)

    items: list[dict] = []
    if run is not None:
        cap = settings.alert_policy.max_daily_new_alerts
        records = session.execute(
            select(ScoreRecord).where(ScoreRecord.run_id == run.id)
        ).scalars().all()
        passing = [r for r in records if not r.abstained and r.gate and r.gate.get("passed")]
        by_horizon: dict[str, list[ScoreRecord]] = {}
        for rec in passing:
            by_horizon.setdefault(rec.horizon, []).append(rec)
        for horizon in ("short", "medium", "long"):
            ranked = sorted(
                by_horizon.get(horizon, []), key=lambda r: r.opportunity, reverse=True
            )[:cap]
            for rank, rec in enumerate(ranked, start=1):
                inst = session.get(Instrument, rec.instrument_id)
                items.append(
                    {
                        "rank": rank,
                        "horizon": horizon,
                        "instrument_id": rec.instrument_id,
                        "ticker": inst.ticker if inst else str(rec.instrument_id),
                        "name": inst.name if inst else "",
                        "opportunity": round(rec.opportunity, 2),
                        "confidence": round(rec.confidence, 2),
                        "risk": round(rec.risk, 2),
                        "link": company_link(rec.instrument_id, settings),
                    }
                )

    cutoff = now - timedelta(hours=24)
    recent_alerts = session.execute(
        select(Alert).where(Alert.created_at >= cutoff).order_by(Alert.created_at.desc())
    ).scalars().all()

    from vigil.alerts.notify import deep_link

    lines = [f"DAILY DIGEST {run.as_of.isoformat() if run else now.date().isoformat()}"]
    if run is None:
        lines.append("No completed scan available yet.")
    elif not items:
        lines.append("No gate-passing candidates in the latest scan (selective by design).")
    else:
        lines.append(f"Top gate-passing candidates (run #{run.id}):")
        for it in items:
            lines.append(
                f"  [{it['horizon'].upper():6}] {it['ticker']:6} opp {it['opportunity']:.1f} "
                f"conf {it['confidence']:.1f} risk {it['risk']:.1f} -> {it['link']}"
            )
    lines.append(f"New alerts in the last 24h: {len(recent_alerts)}")
    for alert in recent_alerts[:10]:
        lines.append(f"  - {alert.title} -> {deep_link(alert, settings)}")
    lines.append(DISCLAIMER)
    text = "\n".join(lines)

    digest = {
        "type": "daily_digest",
        "as_of": run.as_of.isoformat() if run else None,
        "run_id": run.id if run else None,
        "items": items,
        "new_alerts_24h": len(recent_alerts),
        "text": text,
    }
    digest["delivered"] = _deliver(session, settings, "daily_digest", text, items)  # type: ignore[assignment]
    return digest


# --------------------------------------------------------------------------
# Weekly review
# --------------------------------------------------------------------------


def _latest_close(session: Session, instrument_id: int, as_of: date) -> float | None:
    row = session.execute(
        select(PriceBar.close)
        .where(PriceBar.instrument_id == instrument_id, PriceBar.bar_date <= as_of)
        .order_by(PriceBar.bar_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    return float(row) if row is not None else None


def send_weekly_review(
    session: Session,
    settings: Settings,
    as_of: date | None = None,
    now: datetime | None = None,
) -> dict:
    """Active signals grouped by state; WEAKENING/TRIM first.

    Per signal: current close (as of ``as_of``) vs anchor price, and thesis
    age in days. ``as_of`` defaults to the latest stored bar date.
    """
    now = _naive_utc(now)
    if as_of is None:
        as_of = session.execute(select(func.max(PriceBar.bar_date))).scalar_one() or now.date()

    signals = session.execute(
        select(Signal).where(Signal.active.is_(True)).order_by(Signal.id)
    ).scalars().all()

    items: list[dict] = []
    for sig in signals:
        inst = session.get(Instrument, sig.instrument_id)
        current = _latest_close(session, sig.instrument_id, as_of)
        change_pct = None
        if current is not None and sig.anchor_price:
            change_pct = round((current - sig.anchor_price) / sig.anchor_price * 100, 2)
        items.append(
            {
                "signal_id": sig.id,
                "instrument_id": sig.instrument_id,
                "ticker": inst.ticker if inst else str(sig.instrument_id),
                "family": sig.family,
                "horizon": sig.horizon,
                "state": sig.state,
                "anchor_price": sig.anchor_price,
                "anchor_date": sig.anchor_date.isoformat() if sig.anchor_date else None,
                "current_price": current,
                "price_change_pct": change_pct,
                "thesis_age_days": (as_of - sig.created_at.date()).days,
                "link": company_link(sig.instrument_id, settings),
            }
        )
    items.sort(key=lambda it: (_STATE_ORDER.get(it["state"], 2), it["ticker"], it["signal_id"]))

    by_state: dict[str, list[dict]] = {}
    for it in items:
        by_state.setdefault(it["state"], []).append(it)

    lines = [f"WEEKLY REVIEW {as_of.isoformat()} — {len(items)} active signal(s)"]
    for state in sorted(by_state, key=lambda s: (_STATE_ORDER.get(s, 2), s)):
        lines.append(f"{state} ({len(by_state[state])}):")
        for it in by_state[state]:
            move = (
                f"{it['price_change_pct']:+.1f}% vs anchor"
                if it["price_change_pct"] is not None
                else "no price data"
            )
            lines.append(
                f"  {it['ticker']:6} {it['family']} [{it['horizon']}] {move}, "
                f"thesis {it['thesis_age_days']}d -> {it['link']}"
            )
    if not items:
        lines.append("No active signals.")
    lines.append(DISCLAIMER)
    text = "\n".join(lines)

    digest = {
        "type": "weekly_review",
        "as_of": as_of.isoformat(),
        "items": items,
        "by_state": by_state,
        "text": text,
    }
    digest["delivered"] = _deliver(session, settings, "weekly_review", text, items)
    return digest


# --------------------------------------------------------------------------
# Catalyst reminders
# --------------------------------------------------------------------------


def _relevant_instrument_ids(session: Session) -> set[int]:
    """Owned, watchlisted, or carrying an active signal."""
    ids: set[int] = set()
    for stmt in (
        select(PortfolioPosition.instrument_id).where(PortfolioPosition.active.is_(True)),
        select(WatchlistItem.instrument_id).where(WatchlistItem.active.is_(True)),
        select(Signal.instrument_id).where(Signal.active.is_(True)),
    ):
        ids.update(session.execute(stmt).scalars().all())
    return ids


def send_catalyst_reminders(
    session: Session,
    settings: Settings,
    days: int = 7,
    as_of: date | None = None,
) -> dict:
    """Unresolved catalysts due within ``days`` for relevant instruments only.

    Relevant = owned, watchlisted, or with an active signal. Nothing is
    delivered when there are no upcoming catalysts (no reminder spam).
    """
    if as_of is None:
        as_of = date.today()
    relevant = _relevant_instrument_ids(session)

    items: list[dict] = []
    if relevant:
        catalysts = session.execute(
            select(Catalyst)
            .where(
                Catalyst.resolved.is_(False),
                Catalyst.expected_date >= as_of,
                Catalyst.expected_date <= as_of + timedelta(days=days),
                Catalyst.instrument_id.in_(relevant),
            )
            .order_by(Catalyst.expected_date, Catalyst.instrument_id)
        ).scalars().all()
        for cat in catalysts:
            inst = session.get(Instrument, cat.instrument_id)
            items.append(
                {
                    "catalyst_id": cat.id,
                    "instrument_id": cat.instrument_id,
                    "ticker": inst.ticker if inst else str(cat.instrument_id),
                    "kind": cat.kind,
                    "expected_date": cat.expected_date.isoformat(),
                    "days_until": (cat.expected_date - as_of).days,
                    "date_confirmed": cat.date_confirmed,
                    "binary": cat.binary,
                    "description": cat.description,
                    "link": company_link(cat.instrument_id, settings),
                }
            )

    lines = [f"CATALYST REMINDER {as_of.isoformat()} — next {days} day(s)"]
    for it in items:
        confirmed = "confirmed" if it["date_confirmed"] else "estimated"
        binary = ", binary event" if it["binary"] else ""
        lines.append(
            f"  {it['expected_date']} ({it['days_until']}d, {confirmed}{binary}) "
            f"{it['ticker']:6} {it['kind']}: {it['description']} -> {it['link']}"
        )
    lines.append(DISCLAIMER)
    text = "\n".join(lines)

    digest = {
        "type": "catalyst_reminder",
        "as_of": as_of.isoformat(),
        "window_days": days,
        "items": items,
        "text": text,
    }
    digest["delivered"] = (
        _deliver(session, settings, "catalyst_reminder", text, items) if items else {}
    )
    return digest
