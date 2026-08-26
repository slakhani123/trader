"""Digest job tests: deterministic outputs from a seeded SQLite database."""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from vigil.config import get_settings
from vigil.jobs import digests
from vigil.models import (
    Alert,
    Catalyst,
    Instrument,
    NotificationDelivery,
    PortfolioPosition,
    PriceBar,
    ScoreBundleRow,
    ScoreRecord,
    ScoreRun,
    Signal,
    WatchlistItem,
)

AS_OF = date(2026, 8, 25)
NOW = datetime(2026, 8, 26, 7, 0)


# --------------------------------------------------------------------------
# Seed helpers
# --------------------------------------------------------------------------


def _instrument(session: Session, ticker: str) -> Instrument:
    inst = Instrument(
        ticker=ticker, exchange="NYSE", market="US", name=f"{ticker} Corp",
        sector="Technology", industry="Software", currency="USD",
    )
    session.add(inst)
    session.flush()
    return inst


def _run(session: Session, as_of: date = AS_OF, status: str = "ok") -> ScoreRun:
    run = ScoreRun(
        as_of=as_of, model_version="v1.0.0", config_hash="deadbeef",
        trigger="eod", status=status,
    )
    session.add(run)
    session.flush()
    return run


def _record(
    session: Session,
    run: ScoreRun,
    inst: Instrument,
    horizon: str = "medium",
    opportunity: float = 7.5,
    passed: bool = True,
    abstained: bool = False,
) -> ScoreRecord:
    bundle = session.execute(
        select(ScoreBundleRow).where(
            ScoreBundleRow.run_id == run.id, ScoreBundleRow.instrument_id == inst.id
        )
    ).scalars().first()
    if bundle is None:
        bundle = ScoreBundleRow(
            run_id=run.id, instrument_id=inst.id, as_of=run.as_of,
            model_version=run.model_version,
        )
        session.add(bundle)
        session.flush()
    rec = ScoreRecord(
        run_id=run.id, bundle_id=bundle.id, instrument_id=inst.id, as_of=run.as_of,
        horizon=horizon, opportunity=opportunity, confidence=6.4, risk=4.0,
        abstained=abstained,
        gate={
            "passed": passed,
            "failures": [] if passed else ["min_opportunity"],
            "reward_risk": 2.4,
        },
    )
    session.add(rec)
    session.flush()
    return rec


def _signal(
    session: Session,
    run: ScoreRun,
    inst: Instrument,
    state: str = "TRIGGERED",
    anchor: float = 100.0,
    created: datetime = datetime(2026, 8, 1, 12, 0),
) -> Signal:
    sig = Signal(
        instrument_id=inst.id, family="BREAKOUT", horizon="medium", state=state,
        created_at=created, updated_at=created, first_run_id=run.id,
        last_run_id=run.id, anchor_price=anchor, anchor_date=date(2026, 8, 1),
        active=True,
    )
    session.add(sig)
    session.flush()
    return sig


def _bar(session: Session, inst: Instrument, close: float, bar_date: date = AS_OF) -> None:
    session.add(
        PriceBar(
            instrument_id=inst.id, bar_date=bar_date, open=close, high=close * 1.01,
            low=close * 0.99, close=close, volume=1e6, currency="USD",
        )
    )
    session.flush()


def _deliveries(session: Session) -> list[NotificationDelivery]:
    return list(session.execute(select(NotificationDelivery)).scalars())


# --------------------------------------------------------------------------
# Daily digest
# --------------------------------------------------------------------------


def test_daily_digest_structure_and_delivery(session: Session) -> None:
    settings = get_settings()
    aaa, bbb = _instrument(session, "AAA"), _instrument(session, "BBB")
    run = _run(session)
    _record(session, run, aaa, horizon="medium", opportunity=8.2, passed=True)
    _record(session, run, bbb, horizon="medium", opportunity=7.1, passed=True)
    _record(session, run, bbb, horizon="short", opportunity=6.9, passed=False)  # gate-failing
    sig = _signal(session, run, aaa)
    session.add_all(
        [
            Alert(
                signal_id=sig.id, instrument_id=aaa.id, run_id=run.id, as_of=AS_OF,
                family="BREAKOUT", lifecycle_state="TRIGGERED",
                transition="WATCHING->TRIGGERED", horizon="medium",
                title="AAA breakout confirmed", payload={"scores": {}},
                created_at=datetime(2026, 8, 25, 21, 30),  # inside 24h window
            ),
            Alert(
                signal_id=sig.id, instrument_id=aaa.id, run_id=run.id, as_of=AS_OF,
                family="BREAKOUT", lifecycle_state="WATCHING", transition="",
                horizon="medium", title="AAA watch", payload={"scores": {}},
                created_at=datetime(2026, 8, 24, 21, 30),  # older than 24h
            ),
        ]
    )
    session.flush()

    digest = digests.send_daily_digest(session, settings, now=NOW)

    assert digest["type"] == "daily_digest"
    assert digest["run_id"] == run.id
    assert digest["as_of"] == AS_OF.isoformat()
    # Gate-passing only, ranked by opportunity within the horizon.
    assert [it["ticker"] for it in digest["items"]] == ["AAA", "BBB"]
    assert all(it["horizon"] == "medium" for it in digest["items"])
    assert digest["items"][0]["link"].endswith(f"/companies/{aaa.id}")
    assert digest["new_alerts_24h"] == 1
    assert digest["text"].startswith("DAILY DIGEST")
    assert "AAA breakout confirmed" in digest["text"]

    inapp = [d for d in _deliveries(session) if d.channel == "inapp"]
    assert len(inapp) == 1
    assert inapp[0].alert_id is None
    assert inapp[0].detail.startswith("DAILY DIGEST")
    assert len(inapp[0].detail) <= 400
    assert digest["delivered"] == {"inapp": "sent"}  # webhook not configured


def test_daily_digest_caps_items_per_horizon(session: Session) -> None:
    settings = get_settings().model_copy(deep=True)
    settings.alert_policy.max_daily_new_alerts = 1
    low, high = _instrument(session, "LOW"), _instrument(session, "HIGH")
    run = _run(session)
    _record(session, run, low, horizon="long", opportunity=7.0, passed=True)
    _record(session, run, high, horizon="long", opportunity=9.0, passed=True)

    digest = digests.send_daily_digest(session, settings, now=NOW)

    assert [it["ticker"] for it in digest["items"]] == ["HIGH"]


def test_daily_digest_without_runs(session: Session) -> None:
    digest = digests.send_daily_digest(session, get_settings(), now=NOW)
    assert digest["run_id"] is None
    assert digest["items"] == []
    assert digest["new_alerts_24h"] == 0
    assert len(_deliveries(session)) == 1  # digest still recorded in-app


def test_daily_digest_posts_webhook(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings().model_copy(deep=True)
    settings.webhook_url = "https://hooks.example.test/vigil"
    calls: dict = {}

    class FakeResponse:
        status_code = 200

    def fake_post(url: str, json: dict | None = None, timeout: float | None = None):
        calls["url"], calls["json"] = url, json
        return FakeResponse()

    monkeypatch.setattr(digests.httpx, "post", fake_post)
    inst = _instrument(session, "AAA")
    run = _run(session)
    _record(session, run, inst, opportunity=8.0, passed=True)

    digest = digests.send_daily_digest(session, settings, now=NOW)

    assert calls["url"] == settings.webhook_url
    assert calls["json"]["type"] == "daily_digest"
    assert calls["json"]["items"][0]["ticker"] == "AAA"
    json.dumps(calls["json"])  # payload must be JSON-serializable
    assert digest["delivered"] == {"inapp": "sent", "webhook": "sent"}
    assert {d.channel for d in _deliveries(session)} == {"inapp", "webhook"}


# --------------------------------------------------------------------------
# Weekly review
# --------------------------------------------------------------------------


def test_weekly_review_orders_weakening_first(session: Session) -> None:
    settings = get_settings()
    aaa, bbb = _instrument(session, "AAA"), _instrument(session, "BBB")
    run = _run(session)
    _signal(session, run, aaa, state="TRIGGERED", anchor=100.0)
    _signal(session, run, bbb, state="WEAKENING", anchor=100.0,
            created=datetime(2026, 7, 26, 9, 0))
    _bar(session, aaa, close=110.0)
    _bar(session, bbb, close=90.0)

    digest = digests.send_weekly_review(session, settings, as_of=AS_OF, now=NOW)

    assert digest["type"] == "weekly_review"
    assert digest["as_of"] == AS_OF.isoformat()
    assert len(digest["items"]) == 2
    first, second = digest["items"]
    assert first["state"] == "WEAKENING"  # deteriorating theses come first
    assert first["ticker"] == "BBB"
    assert first["price_change_pct"] == pytest.approx(-10.0)
    assert first["thesis_age_days"] == 30
    assert second["price_change_pct"] == pytest.approx(10.0)
    assert set(digest["by_state"]) == {"TRIGGERED", "WEAKENING"}
    assert digest["text"].startswith("WEEKLY REVIEW")

    inapp = [d for d in _deliveries(session) if d.channel == "inapp"]
    assert len(inapp) == 1
    assert inapp[0].detail.startswith("WEEKLY REVIEW")


def test_weekly_review_no_signals(session: Session) -> None:
    digest = digests.send_weekly_review(session, get_settings(), as_of=AS_OF, now=NOW)
    assert digest["items"] == []
    assert "No active signals" in digest["text"]


# --------------------------------------------------------------------------
# Catalyst reminders
# --------------------------------------------------------------------------


def test_catalyst_reminders_scope_and_window(session: Session) -> None:
    settings = get_settings()
    watched = _instrument(session, "WCH")
    owned = _instrument(session, "OWN")
    signalled = _instrument(session, "SIG")
    other = _instrument(session, "OTH")
    run = _run(session)
    session.add(WatchlistItem(instrument_id=watched.id, active=True))
    session.add(
        PortfolioPosition(
            instrument_id=owned.id, quantity=10, avg_cost_local=50.0,
            currency="USD", opened_at=date(2026, 1, 5), active=True,
        )
    )
    _signal(session, run, signalled)
    session.add_all(
        [
            Catalyst(external_id="c1", instrument_id=watched.id, kind="earnings",
                     expected_date=date(2026, 8, 28), date_confirmed=True,
                     description="Q2 results", binary=True),
            Catalyst(external_id="c2", instrument_id=owned.id, kind="earnings",
                     expected_date=date(2026, 8, 26), description="Q2 results"),
            Catalyst(external_id="c3", instrument_id=signalled.id, kind="regulatory",
                     expected_date=date(2026, 8, 31), description="FDA decision"),
            # Outside the 7-day window: excluded.
            Catalyst(external_id="c4", instrument_id=owned.id, kind="earnings",
                     expected_date=date(2026, 9, 20), description="Q3 results"),
            # Not owned/watchlisted/signalled: excluded.
            Catalyst(external_id="c5", instrument_id=other.id, kind="earnings",
                     expected_date=date(2026, 8, 27), description="Q2 results"),
            # Already resolved: excluded.
            Catalyst(external_id="c6", instrument_id=watched.id, kind="product",
                     expected_date=date(2026, 8, 29), description="Launch",
                     resolved=True),
        ]
    )
    session.flush()

    digest = digests.send_catalyst_reminders(session, settings, days=7, as_of=AS_OF)

    assert digest["type"] == "catalyst_reminder"
    assert [it["ticker"] for it in digest["items"]] == ["OWN", "WCH", "SIG"]  # by date
    first = digest["items"][0]
    assert first["expected_date"] == "2026-08-26"
    assert first["days_until"] == 1
    assert first["link"].endswith(f"/companies/{owned.id}")
    wch = digest["items"][1]
    assert wch["date_confirmed"] is True
    assert wch["binary"] is True

    inapp = [d for d in _deliveries(session) if d.channel == "inapp"]
    assert len(inapp) == 1
    assert inapp[0].detail.startswith("CATALYST REMINDER")


def test_catalyst_reminders_empty_skips_delivery(session: Session) -> None:
    digest = digests.send_catalyst_reminders(session, get_settings(), as_of=AS_OF)
    assert digest["items"] == []
    assert digest["delivered"] == {}
    assert _deliveries(session) == []
