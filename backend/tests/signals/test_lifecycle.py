"""Lifecycle FSM: creation, transitions, cooldown/dedup, stops, invalidation."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tests.factories import make_snapshot, price_frame, quarterly_fundamentals
from vigil.config import Settings
from vigil.schemas.core import (
    EngineResult,
    EntryPlan,
    HorizonScore,
    ScoreBundle,
    SignalCandidate,
    SignalFamily,
)
from vigil.signals.lifecycle import sync_signals

AS_OF = date(2026, 8, 25)


def hscore(opportunity: float, confidence: float = 7.0, risk: float = 4.0,
           gate_passed: bool = True) -> HorizonScore:
    from vigil.schemas.core import GateResult

    return HorizonScore(
        horizon="medium", opportunity=opportunity, confidence=confidence, risk=risk,
        components={"valuation": opportunity},
        gate=GateResult(passed=gate_passed, failures=[] if gate_passed else ["x"]),
    )


def bundle_for(snapshot, opportunity: float = 7.5, engines: dict | None = None) -> ScoreBundle:
    return ScoreBundle(
        instrument_id=snapshot.info.instrument_id,
        as_of=snapshot.as_of,
        model_version="v1.0.0",
        horizons={
            "short": hscore(opportunity).model_copy(update={"horizon": "short"}),
            "medium": hscore(opportunity),
            "long": hscore(opportunity).model_copy(update={"horizon": "long"}),
        },
        engine_results=engines or {},
    )


def candidate(snapshot, bundle, family=SignalFamily.DEEP_VALUE, horizon="medium",
              watch=False, stop: float | None = None) -> SignalCandidate:
    return SignalCandidate(
        family=family, horizon=horizon, instrument_id=snapshot.info.instrument_id,
        as_of=bundle.as_of, state_hint="WATCHING" if watch else "TRIGGERED",
        scores=bundle.horizons[horizon],
        entry_plan=EntryPlan(zone_low=90, zone_high=100, stop=stop, target_low=130,
                             target_high=150),
        rationale=["✓ test condition"],
    )


@pytest.fixture()
def env(session):
    from vigil.models import Instrument, ScoreRun

    inst = Instrument(
        ticker="LIFE", exchange="NYSE", market="US", name="Lifecycle Corp",
        sector="Technology", industry="Software", currency="USD",
    )
    session.add(inst)
    for _ in range(2):  # run ids 1 and 2 for the two-scan tests
        session.add(
            ScoreRun(as_of=AS_OF, model_version="v1.0.0", config_hash="test", status="ok")
        )
    session.flush()
    snap = make_snapshot(
        ticker="LIFE", fundamentals=quarterly_fundamentals(), as_of=AS_OF,
    )
    object.__setattr__(snap.info, "instrument_id", inst.id)
    return session, snap, Settings(debug=True)


class TestCreationAndTransitions:
    def test_new_candidate_creates_triggered_signal_and_alert(self, env):
        session, snap, settings = env
        bundle = bundle_for(snap)
        drafts = sync_signals(session, 1, snap, bundle, [candidate(snap, bundle)], settings)
        assert len(drafts) == 1
        assert drafts[0].transition == "NEW→TRIGGERED"
        assert drafts[0].signal.state == "TRIGGERED"

    def test_watch_candidate_creates_watching(self, env):
        session, snap, settings = env
        bundle = bundle_for(snap)
        drafts = sync_signals(
            session, 1, snap, bundle, [candidate(snap, bundle, watch=True)], settings
        )
        assert drafts[0].state.value == "WATCHING"

    def test_watching_promotes_to_triggered(self, env):
        session, snap, settings = env
        bundle = bundle_for(snap)
        sync_signals(session, 1, snap, bundle, [candidate(snap, bundle, watch=True)], settings)
        drafts = sync_signals(session, 2, snap, bundle, [candidate(snap, bundle)], settings)
        assert any(d.transition == "WATCHING→TRIGGERED" for d in drafts)

    def test_weakening_on_score_decay(self, env):
        session, snap, settings = env
        strong = bundle_for(snap, 8.0)
        sync_signals(session, 1, snap, strong, [candidate(snap, strong)], settings)
        # simulate the alert bookkeeping the builder would do
        from sqlalchemy import select

        from vigil.models import Signal

        sig = session.execute(select(Signal)).scalar_one()
        sig.last_alert_opportunity = 8.0
        weak = bundle_for(snap, 6.0, )
        weak.horizons["medium"].gate.passed = False
        weak.horizons["medium"] = weak.horizons["medium"].model_copy(update={"confidence": 4.0})
        drafts = sync_signals(session, 2, snap, weak, [], settings)
        assert any(d.state.value == "WEAKENING" for d in drafts)


class TestStopsAndInvalidation:
    def test_confirmed_stop_breach_exits(self, env):
        session, snap, settings = env
        bundle = bundle_for(snap)
        # Stop above the last two closes => two consecutive closes below.
        stop = float(snap.prices["close"].iloc[-2:].max()) * 1.05
        sync_signals(session, 1, snap, bundle, [candidate(snap, bundle, stop=stop)], settings)
        drafts = sync_signals(session, 2, snap, bundle, [], settings)
        exited = [d for d in drafts if d.state.value == "EXITED"]
        assert exited and "risk stop" in exited[0].reasons[0]

    def test_single_close_below_stop_does_not_exit(self, env):
        """Price volatility is not thesis failure: one close below = no exit."""
        session, snap, settings = env
        closes = snap.prices["close"]
        stop = (float(closes.iloc[-1]) + float(closes.iloc[-2])) / 2
        # stop sits between the last two closes -> only one close below
        assert (float(closes.iloc[-1]) < stop) != (float(closes.iloc[-2]) < stop)
        bundle = bundle_for(snap)
        sync_signals(session, 1, snap, bundle, [candidate(snap, bundle, stop=stop)], settings)
        drafts = sync_signals(session, 2, snap, bundle, [], settings)
        assert not any(d.state.value in ("EXITED", "INVALIDATED") for d in drafts)

    def test_restatement_invalidates(self, env):
        session, snap, settings = env
        bundle = bundle_for(snap)
        sync_signals(session, 1, snap, bundle, [candidate(snap, bundle)], settings)
        flagged = bundle_for(
            snap, 7.5,
            engines={
                "quality": EngineResult(
                    engine="quality", score=5.0,
                    details={"red_flags": ["restatement published for FY2025"]},
                )
            },
        )
        drafts = sync_signals(session, 2, snap, flagged, [], settings)
        inv = [d for d in drafts if d.state.value == "INVALIDATED"]
        assert inv and "restatement" in inv[0].reasons[0]


class TestCooldownAndDedup:
    def test_no_realert_without_material_change(self, env):
        session, snap, settings = env
        bundle = bundle_for(snap)
        sync_signals(session, 1, snap, bundle, [candidate(snap, bundle)], settings)
        from sqlalchemy import select

        from vigil.models import Signal

        sig = session.execute(select(Signal)).scalar_one()
        sig.last_alert_at = None  # pretend alert bookkeeping happened long ago
        sig.last_alert_opportunity = 7.5
        sig.last_alert_risk = 4.0
        sig.last_alert_price = snap.last_close
        drafts = sync_signals(session, 2, snap, bundle, [candidate(snap, bundle)], settings)
        assert drafts == []  # same state, nothing material changed

    def test_cooldown_blocks_even_material_change(self, env):
        session, snap, settings = env
        bundle = bundle_for(snap)
        sync_signals(session, 1, snap, bundle, [candidate(snap, bundle)], settings)
        from datetime import datetime

        from sqlalchemy import select

        from vigil.models import Signal

        sig = session.execute(select(Signal)).scalar_one()
        sig.last_alert_at = datetime.combine(AS_OF - timedelta(days=1), datetime.min.time())
        sig.last_alert_opportunity = 7.5
        sig.last_alert_risk = 4.0
        sig.last_alert_price = (snap.last_close or 100) * 0.90  # >5% price move
        # material change exists but cooldown (5d) not elapsed and state static
        moved = bundle_for(snap, 7.5)
        drafts = sync_signals(session, 2, snap, moved, [candidate(snap, moved)], settings)
        assert drafts == []

    def test_expiry_of_watching(self, env):
        session, snap, settings = env
        bundle = bundle_for(snap)
        sync_signals(session, 1, snap, bundle, [candidate(snap, bundle, watch=True)], settings)
        from sqlalchemy import select

        from vigil.models import Signal

        sig = session.execute(select(Signal)).scalar_one()
        sig.expires_at = AS_OF - timedelta(days=1)
        drafts = sync_signals(session, 2, snap, bundle, [], settings)
        assert any(d.state.value == "EXPIRED" for d in drafts)
        assert not sig.active


class TestPriceFrameSanity:
    def test_factory_positive_prices(self):
        df = price_frame()
        assert (df["close"] > 0).all()
