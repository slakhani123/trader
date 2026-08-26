"""Alert builder: renders an AlertDraft into the full immutable AlertPayload
and persists it. Alerts are the shadow/paper record — never rewritten."""

from __future__ import annotations

from datetime import UTC, datetime, time

from sqlalchemy.orm import Session

from vigil.config import Settings
from vigil.llm.narrative import compose
from vigil.llm.packet import build_packet
from vigil.models import Alert
from vigil.schemas.alerts import (
    AlertPayload,
    CatalystView,
    ChangeSincePrevious,
    CompanyHeader,
    PriceStamp,
    ScoreView,
    SourceLine,
    TechnicalSummary,
    ValuationSummary,
)
from vigil.schemas.core import Evidence, InstrumentSnapshot, ScoreBundle
from vigil.signals.lifecycle import AlertDraft


def _score_view(bundle: ScoreBundle, horizon: str) -> ScoreView:
    h = bundle.horizons[horizon]
    return ScoreView(
        horizon=horizon,
        opportunity=h.opportunity,
        confidence=h.confidence,
        risk=h.risk,
        components=h.components,
        explanation=h.explanation,
    )


def _sources(evidence: list[Evidence], cap: int = 25) -> list[SourceLine]:
    seen: set[str] = set()
    out: list[SourceLine] = []
    for e in evidence:
        key = f"{e.source.provider}|{e.source.reference}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            SourceLine(
                provider=e.source.provider,
                source_type=e.source.source_type,
                reference=e.source.reference,
                published_at=e.source.published_at,
                freshness_days=e.source.freshness_days,
            )
        )
        if len(out) >= cap:
            break
    return out


def _catalyst_views(bundle: ScoreBundle) -> list[CatalystView]:
    cat = bundle.engine_results.get("catalyst")
    views: list[CatalystView] = []
    if cat is None:
        return views
    for c in cat.details.get("upcoming", [])[:8]:
        try:
            views.append(
                CatalystView(
                    kind=str(c["kind"]),
                    date=c["date"],
                    days=int(c["days"]),
                    confirmed=bool(c.get("confirmed", False)),
                    binary=bool(c.get("binary", False)),
                    description=str(c.get("description", "")),
                    priced_in_pct=c.get("priced_in_pct"),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return views


def build_alert(
    session: Session,
    run_id: int,
    snapshot: InstrumentSnapshot,
    bundle: ScoreBundle,
    draft: AlertDraft,
    settings: Settings,
) -> Alert:
    info = snapshot.info
    horizon = draft.horizon
    plan = draft.candidate.entry_plan if draft.candidate else None
    stored_plan = draft.signal.entry_plan or {}
    price = snapshot.last_close
    catalyst_views = _catalyst_views(bundle)

    packet = build_packet(
        snapshot_info=info.model_dump(),
        candidate=draft.candidate,
        bundle=bundle,
        family=draft.family.value,
        horizon=horizon,
        state=draft.state.value,
        transition=draft.transition,
        reasons=draft.reasons,
        changed=draft.changed,
        price=price,
        catalysts=[c.model_dump(mode="json") for c in catalyst_views],
    )
    thesis, summary, narrative_source = compose(packet, settings)

    val = bundle.engine_results.get("valuation")
    val_details = val.details if val else {}
    tech = bundle.engine_results.get("technical")
    tech_details = tech.details if tech else {}
    cat = bundle.engine_results.get("catalyst")

    supporting = draft.candidate.supporting if draft.candidate else [
        e for e in bundle.evidence if e.direction == "supports"
    ][:8]
    contradicting = draft.candidate.contradicting if draft.candidate else [
        e for e in bundle.evidence if e.direction == "contradicts"
    ][:8]

    binary_warning = None
    if cat is not None:
        nb = cat.details.get("next_binary")
        if isinstance(nb, dict) and isinstance(nb.get("days"), int | float) and nb["days"] <= 30:
            binary_warning = (
                f"Binary event ahead: {nb.get('kind', 'event')} expected "
                f"{nb.get('date', '?')} (~{int(nb['days'])} days). Outcome may dominate "
                "this setup in either direction."
            )
        elif (
            (ne := cat.details.get("next_earnings"))
            and isinstance(ne, dict)
            and isinstance(ne.get("days"), int | float)
            and ne["days"] <= 15
        ):
            binary_warning = f"Earnings expected {ne.get('date', '?')} (~{int(ne['days'])} days)."

    entry_zone = None
    p = plan or None
    zone_low = (p.zone_low if p else stored_plan.get("zone_low"))
    zone_high = (p.zone_high if p else stored_plan.get("zone_high"))
    if isinstance(zone_low, int | float) and isinstance(zone_high, int | float):
        entry_zone = {"low": zone_low, "high": zone_high}
    target_range = None
    t_low = p.target_low if p else stored_plan.get("target_low")
    t_high = p.target_high if p else stored_plan.get("target_high")
    if isinstance(t_low, int | float):
        target_range = {"low": t_low, "high": t_high if isinstance(t_high, int | float) else t_low}

    last_bar = snapshot.last_price_date
    payload = AlertPayload(
        company=CompanyHeader(
            name=info.name, ticker=info.ticker, exchange=info.exchange, market=info.market,
            sector=info.sector, industry=info.industry,
            market_cap_local=snapshot.liquidity.market_cap_local,
            market_cap_base=snapshot.liquidity.market_cap_base,
            base_currency=settings.base_currency, local_currency=info.currency,
        ),
        signal_family=draft.family.value,
        lifecycle_state=draft.state.value,
        transition=draft.transition,
        best_fit_horizon=bundle.best_fit_horizon,
        horizon=horizon,
        priority=draft.priority,
        price=PriceStamp(
            price=price or 0.0,
            currency=info.currency,
            as_of_date=bundle.as_of,
            bar_timestamp=datetime.combine(last_bar, time(21, 0)) if last_bar else
            datetime.combine(bundle.as_of, time(0, 0)),
            staleness_trading_days=snapshot.liquidity.price_staleness_days,
            fx_to_base=snapshot.fx_to_base,
            fx_as_of=snapshot.fx_as_of,
        ),
        scores=_score_view(bundle, horizon),
        all_horizons={h: _score_view(bundle, h) for h in bundle.horizons},
        change=ChangeSincePrevious(
            previous_alert_at=draft.signal.last_alert_at,
            previous_state=draft.transition.split("→")[0] if "→" in draft.transition else None,
            opportunity_delta=(
                round(bundle.horizons[horizon].opportunity - draft.signal.last_alert_opportunity, 2)
                if draft.signal.last_alert_opportunity is not None else None
            ),
            risk_delta=(
                round(bundle.horizons[horizon].risk - draft.signal.last_alert_risk, 2)
                if draft.signal.last_alert_risk is not None else None
            ),
            price_change_pct=(
                round((price / draft.signal.last_alert_price - 1) * 100, 2)
                if price and draft.signal.last_alert_price else None
            ),
            changed=draft.changed or draft.reasons[:3],
        ),
        thesis=thesis,
        thesis_summary=summary,
        narrative_source=narrative_source,
        supporting=supporting,
        contradicting=contradicting,
        valuation=ValuationSummary(
            primary_multiple=val_details.get("primary_multiple"),
            multiples={
                k: round(v, 3)
                for k, v in (val_details.get("multiples") or {}).items()
                if isinstance(v, int | float)
            },
            vs_history_percentile=val_details.get("vs_history_percentile"),
            vs_peers_note=val_details.get("vs_peers_note"),
            analyst_target=val_details.get("target_summary"),
            fair_value_low=val_details.get("fair_value_low"),
            fair_value_high=val_details.get("fair_value_high"),
        ),
        technicals=TechnicalSummary(
            trend_state=tech_details.get("trend_state"),
            support_zones=(tech_details.get("support_zones") or [])[:4],
            resistance_levels=[
                r for r in (tech_details.get("resistance_levels") or [])[:4]
                if isinstance(r, int | float)
            ],
            rsi14=tech_details.get("rsi14"),
            atr_pct=tech_details.get("atr_pct"),
            reward_risk=tech_details.get("reward_risk"),
            notes=[w for w in (tech.warnings if tech else [])][:4],
        ),
        catalysts=catalyst_views,
        entry_zone=entry_zone,
        conditions_before_entry=(
            p.conditions_before_entry if p else stored_plan.get("conditions_before_entry", [])
        ),
        invalidation_conditions=(
            p.invalidation_conditions if p else stored_plan.get("invalidation_conditions", [])
        ),
        fundamental_invalidation=(
            p.fundamental_invalidation if p else stored_plan.get("fundamental_invalidation", [])
        ),
        stop=(p.stop if p else stored_plan.get("stop")),
        scenarios=(p.scenarios if p else []),
        target_range=target_range,
        trim_conditions=(p.trim_conditions if p else stored_plan.get("trim_conditions", [])),
        exit_conditions=(p.exit_conditions if p else stored_plan.get("exit_conditions", [])),
        binary_event_warning=binary_warning,
        data_warnings=snapshot.quality.warnings + bundle.warnings[:6],
        missing_data=snapshot.quality.missing,
        sources=_sources(supporting + contradicting + bundle.evidence),
        model_version=bundle.model_version,
        generated_at=datetime.now(UTC).replace(tzinfo=None),
    )

    title = (
        f"{info.ticker} · {draft.family.value.replace('_', ' ')} · {draft.state.value}"
        f" · {horizon} · O {payload.scores.opportunity:.1f} / C {payload.scores.confidence:.1f}"
        f" / R {payload.scores.risk:.1f}"
    )
    alert = Alert(
        signal_id=draft.signal.id,
        instrument_id=info.instrument_id,
        run_id=run_id,
        as_of=bundle.as_of,
        family=draft.family.value,
        lifecycle_state=draft.state.value,
        transition=draft.transition,
        horizon=horizon,
        priority=draft.priority,
        title=title[:240],
        payload=payload.model_dump(mode="json"),
        narrative_source=narrative_source,
    )
    session.add(alert)
    session.flush()

    # Bookkeeping on the signal (these are the only mutable alert-related fields).
    now = datetime.now(UTC).replace(tzinfo=None)
    draft.signal.last_alert_at = now
    draft.signal.last_alert_opportunity = bundle.horizons[horizon].opportunity
    draft.signal.last_alert_risk = bundle.horizons[horizon].risk
    draft.signal.last_alert_price = price
    return alert
