"""Signal lifecycle state machine.

    WATCHING → TRIGGERED → REINFORCED → WEAKENING → TRIM → EXITED
                                                        ↘ INVALIDATED
    WATCHING → EXPIRED (never confirmed)

Recovery edges: WEAKENING → REINFORCED, TRIM → REINFORCED (documented).

Alert policy: a state TRANSITION always alerts; a same-state refresh alerts
only when the cooldown has elapsed AND something material changed (score,
price, risk — thresholds in ``settings.alert_policy``). Everything else is
a silent bookkeeping update, so the alert stream stays selective.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from vigil.config import Settings
from vigil.models import Signal
from vigil.schemas.core import (
    InstrumentSnapshot,
    LifecycleState,
    ScoreBundle,
    SignalCandidate,
    SignalFamily,
)

TERMINAL = {LifecycleState.EXITED, LifecycleState.INVALIDATED, LifecycleState.EXPIRED}

# Trading-day life expectancy per horizon once TRIGGERED (calendar days).
SIGNAL_TTL_DAYS = {"short": 45, "medium": 240, "long": 730}


@dataclass
class AlertDraft:
    """Everything the alert builder needs to render one immutable alert."""

    signal: Signal
    family: SignalFamily
    horizon: str
    state: LifecycleState
    transition: str
    reasons: list[str]
    candidate: SignalCandidate | None
    priority: str = "normal"
    changed: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _active_signals(session: Session, instrument_id: int) -> dict[tuple[str, str], Signal]:
    rows = session.execute(
        select(Signal).where(Signal.instrument_id == instrument_id, Signal.active.is_(True))
    ).scalars()
    return {(r.family, r.horizon): r for r in rows}


def _push_history(sig: Signal, state: str, as_of: date, reason: str) -> None:
    hist = list(sig.state_history or [])
    hist.append({"state": state, "as_of": as_of.isoformat(), "reason": reason[:300]})
    sig.state_history = hist
    sig.state = state
    sig.updated_at = _now()


def _material_change(
    sig: Signal, scores, price: float | None, settings: Settings
) -> list[str]:
    """Human-readable list of material changes vs the last alerted state."""
    pol = settings.alert_policy
    changed: list[str] = []
    if sig.last_alert_opportunity is not None:
        delta = scores.opportunity - sig.last_alert_opportunity
        if abs(delta) >= pol.material_score_delta:
            changed.append(f"opportunity moved {delta:+.1f} since the last alert")
    if sig.last_alert_risk is not None:
        rdelta = scores.risk - sig.last_alert_risk
        if abs(rdelta) >= pol.material_risk_delta:
            changed.append(f"risk moved {rdelta:+.1f} since the last alert")
    if sig.last_alert_price and price:
        move = (price / sig.last_alert_price - 1.0) * 100
        if abs(move) >= pol.material_price_move_pct:
            changed.append(f"price moved {move:+.1f}% since the last alert")
    return changed


def _cooldown_ok(sig: Signal, as_of: date, settings: Settings) -> bool:
    if sig.last_alert_at is None:
        return True
    return (as_of - sig.last_alert_at.date()).days >= settings.alert_policy.cooldown_days


def _breached_stop(snapshot: InstrumentSnapshot, stop: float | None) -> bool:
    """Two consecutive closes below the stop = confirmed break."""
    if stop is None or snapshot.prices.empty or len(snapshot.prices) < 2:
        return False
    closes = snapshot.prices["close"].iloc[-2:]
    return bool((closes < stop).all())


def _fundamental_invalidation(bundle: ScoreBundle) -> list[str]:
    reasons: list[str] = []
    q = bundle.engine_results.get("quality")
    if q is not None:
        flags = q.details.get("red_flags") or []
        if any("restat" in f.lower() for f in flags):
            reasons.append("a financial restatement was published")
        if len(flags) >= 3:
            reasons.append(f"accounting red flags accumulated ({len(flags)})")
    v = bundle.engine_results.get("valuation")
    if v is not None:
        trap = v.details.get("value_trap") or {}
        if trap.get("is_trap_risk") and len(trap.get("failed_checks", [])) >= 3:
            reasons.append("value-trap checks now fail on multiple dimensions")
    return reasons


def sync_signals(
    session: Session,
    run_id: int,
    snapshot: InstrumentSnapshot,
    bundle: ScoreBundle,
    candidates: list[SignalCandidate],
    settings: Settings,
) -> list[AlertDraft]:
    """Reconcile fresh candidates with active signals. Returns alert drafts;
    persists signal-state changes (caller commits)."""
    as_of = bundle.as_of
    price = snapshot.last_close
    drafts: list[AlertDraft] = []
    active = _active_signals(session, snapshot.info.instrument_id)
    cands: dict[tuple[str, str], SignalCandidate] = {
        (c.family.value, c.horizon): c for c in candidates
    }

    # --- new candidates without an active signal ---
    for key, new_cand in cands.items():
        if key in active:
            continue
        cand: SignalCandidate | None = new_cand
        assert cand is not None  # narrow for the shared block below
        state = (
            LifecycleState.WATCHING if cand.state_hint == "WATCHING" else LifecycleState.TRIGGERED
        )
        ttl = (
            settings.alert_policy.watch_expiry_days
            if state is LifecycleState.WATCHING
            else SIGNAL_TTL_DAYS[cand.horizon]
        )
        sig = Signal(
            instrument_id=snapshot.info.instrument_id,
            family=cand.family.value,
            horizon=cand.horizon,
            state=state.value,
            first_run_id=run_id,
            last_run_id=run_id,
            anchor_price=price,
            anchor_date=as_of,
            entry_plan=cand.entry_plan.model_dump(mode="json"),
            last_scores=cand.scores.model_dump(mode="json"),
            state_history=[],
            expires_at=as_of + timedelta(days=ttl),
            active=True,
        )
        _push_history(sig, state.value, as_of, "; ".join(cand.rationale[:3]))
        session.add(sig)
        session.flush()
        priority = "high" if state is LifecycleState.TRIGGERED and cand.family in (
            SignalFamily.OVERSOLD_AT_SUPPORT, SignalFamily.BREAKOUT_CONTINUATION,
            SignalFamily.FULL_EXIT,
        ) else ("digest" if cand.family in (SignalFamily.HOLD, SignalFamily.WATCH_SETUP) else "normal")
        drafts.append(
            AlertDraft(
                signal=sig, family=cand.family, horizon=cand.horizon, state=state,
                transition=f"NEW→{state.value}", reasons=cand.rationale,
                candidate=cand, priority=priority,
            )
        )

    # --- existing signals ---
    for key, sig in active.items():
        family = SignalFamily(sig.family)
        cand = cands.get(key)
        sig.last_run_id = run_id
        scores = bundle.horizons.get(sig.horizon)
        if scores is None:
            continue
        stop = (sig.entry_plan or {}).get("stop")
        old_state = LifecycleState(sig.state)
        draft: AlertDraft | None = None

        fundamental_reasons = _fundamental_invalidation(bundle)
        stop_hit = _breached_stop(snapshot, stop if isinstance(stop, int | float) else None)

        if family in (SignalFamily.HOLD, SignalFamily.TRIM, SignalFamily.FULL_EXIT,
                      SignalFamily.AVOID):
            # Stance signals: refresh or retire silently when stance changes.
            if cand is None:
                sig.active = False
                _push_history(sig, LifecycleState.EXPIRED.value, as_of, "stance no longer applies")
            else:
                sig.last_scores = cand.scores.model_dump(mode="json")
                changed = _material_change(sig, cand.scores, price, settings)
                if changed and _cooldown_ok(sig, as_of, settings):
                    draft = AlertDraft(
                        signal=sig, family=family, horizon=sig.horizon, state=old_state,
                        transition=f"{old_state.value} (refresh)", reasons=cand.rationale,
                        candidate=cand, priority="digest", changed=changed,
                    )
            if draft:
                drafts.append(draft)
            continue

        # 1) Invalidation / stop breach dominate everything.
        new_state: LifecycleState | None = None
        if old_state not in TERMINAL and (fundamental_reasons or stop_hit):
            if stop_hit and not fundamental_reasons:
                new_state, reasons = LifecycleState.EXITED, [
                    f"confirmed break of the risk stop {stop:.2f} (two consecutive closes below)"
                ]
            else:
                new_state, reasons = LifecycleState.INVALIDATED, fundamental_reasons or []
                if stop_hit:
                    reasons.append(f"risk stop {stop:.2f} also breached")
            _push_history(sig, new_state.value, as_of, "; ".join(reasons))
            sig.active = False
            drafts.append(
                AlertDraft(
                    signal=sig, family=family, horizon=sig.horizon, state=new_state,
                    transition=f"{old_state.value}→{new_state.value}", reasons=reasons,
                    candidate=cand, priority="high",
                )
            )
            continue

        # 2) Expiry.
        if sig.expires_at is not None and as_of > sig.expires_at:
            if old_state is LifecycleState.WATCHING:
                reasons = ["setup expired without confirmation"]
            else:
                reasons = ["signal horizon elapsed without an exit event"]
            _push_history(sig, LifecycleState.EXPIRED.value, as_of, reasons[0])
            sig.active = False
            drafts.append(
                AlertDraft(
                    signal=sig, family=family, horizon=sig.horizon,
                    state=LifecycleState.EXPIRED,
                    transition=f"{old_state.value}→EXPIRED", reasons=reasons,
                    candidate=cand, priority="digest",
                )
            )
            continue

        # 3) State-specific transitions.
        new_state = None
        reasons = []
        opp = scores.opportunity
        gate_ok = scores.gate is not None and scores.gate.passed

        if old_state is LifecycleState.WATCHING:
            if cand is not None and cand.state_hint == "TRIGGERED":
                new_state = LifecycleState.TRIGGERED
                reasons = cand.rationale
                sig.anchor_price = price
                sig.anchor_date = as_of
                sig.entry_plan = cand.entry_plan.model_dump(mode="json")
                sig.expires_at = as_of + timedelta(days=SIGNAL_TTL_DAYS[sig.horizon])
        elif old_state in (LifecycleState.TRIGGERED, LifecycleState.REINFORCED):
            last_opp = sig.last_alert_opportunity or opp
            trim_now = _trim_conditions(snapshot, bundle, sig, settings)
            if trim_now:
                new_state, reasons = LifecycleState.TRIM, trim_now
            elif opp - last_opp >= settings.alert_policy.material_score_delta and gate_ok:
                if old_state is LifecycleState.TRIGGERED:
                    new_state = LifecycleState.REINFORCED
                    reasons = [f"opportunity strengthened to {opp:.1f} with the gate still passing"]
            elif last_opp - opp >= settings.alert_policy.material_score_delta or (
                not gate_ok and scores.confidence < settings.gates.min_confidence
            ):
                new_state = LifecycleState.WEAKENING
                reasons = [f"opportunity decayed to {opp:.1f}" if last_opp - opp > 0
                           else "confidence fell below the gate"]
        elif old_state is LifecycleState.WEAKENING:
            trim_now = _trim_conditions(snapshot, bundle, sig, settings)
            if opp >= settings.gates.min_opportunity and gate_ok:
                new_state = LifecycleState.REINFORCED
                reasons = [f"setup recovered (opportunity {opp:.1f}, gate passing)"]
            elif trim_now:
                new_state, reasons = LifecycleState.TRIM, trim_now
            elif opp < 4.0:
                new_state = LifecycleState.EXITED
                reasons = [f"setup decayed (opportunity {opp:.1f} < 4.0) — thesis no longer supported"]
        elif old_state is LifecycleState.TRIM:
            if opp < 4.0 or scores.risk >= settings.gates.max_risk:
                new_state = LifecycleState.EXITED
                reasons = ["remaining upside no longer justifies the risk"]
            elif opp >= settings.gates.min_opportunity and gate_ok:
                new_state = LifecycleState.REINFORCED
                reasons = [f"setup re-strengthened (opportunity {opp:.1f})"]

        sig.last_scores = scores.model_dump(mode="json")

        if new_state is not None and new_state is not old_state:
            _push_history(sig, new_state.value, as_of, "; ".join(reasons))
            if new_state in TERMINAL:
                sig.active = False
            priority = "high" if new_state in (
                LifecycleState.TRIM, LifecycleState.EXITED, LifecycleState.INVALIDATED
            ) else "normal"
            drafts.append(
                AlertDraft(
                    signal=sig, family=family, horizon=sig.horizon, state=new_state,
                    transition=f"{old_state.value}→{new_state.value}", reasons=reasons,
                    candidate=cand, priority=priority,
                )
            )
        else:
            # Same state: re-alert only on cooldown + material change.
            changed = _material_change(sig, scores, price, settings)
            if changed and _cooldown_ok(sig, as_of, settings) and cand is not None:
                drafts.append(
                    AlertDraft(
                        signal=sig, family=family, horizon=sig.horizon, state=old_state,
                        transition=f"{old_state.value} (material change)",
                        reasons=cand.rationale, candidate=cand,
                        priority="normal", changed=changed,
                    )
                )
    return drafts


def _trim_conditions(
    snapshot: InstrumentSnapshot,
    bundle: ScoreBundle,
    sig: Signal,
    settings: Settings,
) -> list[str]:
    """Deterministic trim triggers for an open buy-side signal."""
    reasons: list[str] = []
    price = snapshot.last_close
    plan = sig.entry_plan or {}
    target_high = plan.get("target_high")
    if price and isinstance(target_high, int | float) and price >= target_high:
        reasons.append(f"price {price:.2f} reached the target range ceiling {target_high:.2f}")
    tech = bundle.engine_results.get("technical")
    if tech is not None and tech.details.get("extended"):
        reasons.append("position is technically extreme (extension flag)")
    rr = (tech.details.get("reward_risk") if tech else None)
    if isinstance(rr, int | float) and rr < 1.0:
        reasons.append(f"remaining reward/risk {rr:.1f} < 1.0")
    mom = bundle.engine_results.get("momentum")
    if price and sig.anchor_price and mom is not None:
        rose = price / sig.anchor_price - 1.0
        breadth = mom.details.get("revision_breadth_30d")
        if rose > 0.25 and isinstance(breadth, int | float) and breadth < 0:
            reasons.append(
                f"price is up {rose * 100:.0f}% since trigger but estimate revisions "
                "have turned negative (fundamentals not confirming)"
            )
    cat = bundle.engine_results.get("catalyst")
    if cat is not None:
        resolved_score = cat.components.get("recent_outcomes")
        upcoming = cat.details.get("upcoming") or []
        if isinstance(resolved_score, int | float) and resolved_score >= 7 and not upcoming:
            reasons.append("the original catalyst has largely played out with none upcoming")
    return reasons
