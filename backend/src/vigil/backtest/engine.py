"""Point-in-time backtest of the full signal lifecycle.

Replays history by calling the SAME snapshot builder, engines, composite
scorer and signal rules used live at each scan date — no separate formula
copy. Bias controls documented in docs/BACKTESTING.md.

Simulation model:
- Signals are generated on scan-date closes using only then-visible data.
- Entries fill at the NEXT session's open (execution delay), with costs =
  commission + half the liquidity-band spread estimate, per side.
- Open positions are re-evaluated on every scan date with the live trim /
  exit / invalidation logic (reusing ``signals.lifecycle`` internals); a
  price stop is checked on every trading day between scans.
- Delisted names are tradeable until delisting; a delisting closes the
  position at the final bar's close ('delisted' exit).
- Trades are equal-weight for the portfolio curve; per-trade returns use
  fully adjusted prices (splits/dividends) so corporate actions are
  return-correct.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from vigil.backtest.metrics import (
    SimTrade,
    bucketize,
    calibration,
    equity_curve_metrics,
    summarize,
)
from vigil.config import Settings, get_settings
from vigil.data.snapshot import SnapshotBuildError, build_snapshot, load_price_frame
from vigil.engines import run_all_engines
from vigil.models import BacktestRun, BacktestTrade, Instrument
from vigil.models.scoring import Signal as SignalRow
from vigil.schemas.core import LifecycleState, SignalFamily
from vigil.scoring.composite import score_instrument
from vigil.scoring.gates import universe_eligible
from vigil.signals.lifecycle import SIGNAL_TTL_DAYS, _fundamental_invalidation, _trim_conditions
from vigil.signals.rules import generate_candidates

log = logging.getLogger(__name__)

BUY_FAMILIES = {
    SignalFamily.DEEP_VALUE.value,
    SignalFamily.QUALITY_COMPOUNDER.value,
    SignalFamily.OVERSOLD_AT_SUPPORT.value,
    SignalFamily.CONSTRUCTIVE_PULLBACK.value,
    SignalFamily.BREAKOUT_CONTINUATION.value,
    SignalFamily.FUNDAMENTAL_INFLECTION.value,
    SignalFamily.ESTIMATE_MOMENTUM.value,
}


class _OpenPosition:
    """In-memory analogue of a live Signal for lifecycle re-evaluation."""

    def __init__(self, trade: SimTrade, entry_plan: dict, ttl_days: int) -> None:
        self.trade = trade
        self.entry_plan = entry_plan
        self.expires_at = trade.signal_date + timedelta(days=ttl_days)
        # Unsaved Signal ORM instance reuses live trim-condition logic.
        self.sig_like = SignalRow(
            instrument_id=trade.instrument_id,
            family=trade.family,
            horizon=trade.horizon,
            state=LifecycleState.TRIGGERED.value,
            entry_plan=entry_plan,
            anchor_price=trade.entry_price,
            anchor_date=trade.entry_date,
        )


def _price_panel(session: Session, instrument_id: int, end: date) -> pd.DataFrame:
    """Fully adjusted panel to ``end`` for trade accounting (fills, MAE/MFE).
    Uses actions to ``end`` — correct for return computation, never used for
    signal generation."""
    return load_price_frame(session, instrument_id, end)


def _fill(panel: pd.DataFrame, after: date, side: str, cost_frac: float) -> tuple[date, float] | None:
    """First open strictly after ``after``; buy pays up, sell receives less."""
    future = panel.loc[panel.index > pd.Timestamp(after)]
    if future.empty:
        return None
    ts = future.index[0]
    px = float(future["adj_open"].iloc[0]) if "adj_open" in future else float(future["open"].iloc[0])
    px *= (1 + cost_frac) if side == "buy" else (1 - cost_frac)
    return ts.date(), px


def _cost_frac(spread_bps: float | None, settings: Settings) -> float:
    half_spread = (spread_bps or 25.0) / 2 if settings.backtest_costs.slippage_half_spread else 0.0
    return (settings.backtest_costs.commission_bps_per_side + half_spread) / 10_000


def _benchmark_series(session: Session, market: str, end: date) -> pd.Series | None:
    """Benchmark adj_close for a market. More than one index row can exist
    (universe edits add instruments but never delete old ones — ^SPX swapped
    for SPY leaves both); use the one with the longest usable history."""
    rows = session.execute(
        select(Instrument).where(
            Instrument.security_type == "index",
            Instrument.market == market,
            Instrument.sector == "",
        )
    ).scalars()
    best: pd.Series | None = None
    for idx in rows:
        series = _price_panel(session, idx.id, end)["adj_close"]
        if len(series) and (best is None or len(series) > len(best)):
            best = series
    return best


def run_backtest(
    session: Session,
    start: date,
    end: date,
    name: str = "backtest",
    holdout_start: date | None = None,
    step_days: int = 5,
    include_peers: bool = False,
    settings: Settings | None = None,
) -> BacktestRun:
    settings = settings or get_settings()
    run = BacktestRun(
        name=name,
        model_version=settings.scoring_model_version,
        config={
            "step_days": step_days,
            "include_peers": include_peers,
            "costs": settings.backtest_costs.model_dump(),
            "gates": settings.gates.model_dump(),
        },
        start_date=start,
        end_date=end,
        holdout_start=holdout_start,
    )
    session.add(run)
    session.flush()

    instruments = list(
        session.execute(
            select(Instrument).where(Instrument.security_type == "common")
        ).scalars()
    )
    # Adjusted panels + benchmark panels once, for trade accounting.
    panels: dict[int, pd.DataFrame] = {}
    bench_by_market: dict[str, pd.Series] = {}
    for inst in instruments:
        panels[inst.id] = _price_panel(session, inst.id, end)
    for market in {i.market for i in instruments}:
        bench = _benchmark_series(session, market, end)
        if bench is not None:
            bench_by_market[market] = bench

    scan_dates = list(pd.bdate_range(start, end, freq=f"{step_days}B"))
    open_positions: dict[tuple[int, str, str], _OpenPosition] = {}
    closed: list[SimTrade] = []
    scans = 0

    for ts in scan_dates:
        as_of = ts.date()
        # 1) evaluate open positions (stops daily; thesis on scan dates)
        for key, pos in list(open_positions.items()):
            inst_id = key[0]
            panel = panels[inst_id]
            window = panel.loc[
                (panel.index > pd.Timestamp(pos.trade.entry_date))
                & (panel.index <= ts)
            ]
            exit_reason = None
            # daily stop check between scans (two consecutive raw closes below)
            stop = pos.entry_plan.get("stop")
            if isinstance(stop, int | float) and len(window) >= 2:
                below = (window["close"] < stop).astype(int)
                if int((below.rolling(2).sum() == 2).sum()) > 0:
                    exit_reason = "stop"
            # delisting: no more bars coming
            last_bar = panel.index.max()
            if exit_reason is None and last_bar <= ts and last_bar < pd.Timestamp(end):
                exit_reason = "delisted"
            snapshot = bundle = None
            if exit_reason is None:
                try:
                    snapshot = build_snapshot(
                        session, inst_id, as_of, settings, include_peers=include_peers
                    )
                    results = run_all_engines(snapshot, settings)
                    bundle = score_instrument(snapshot, results, settings, as_of)
                except SnapshotBuildError:
                    pass
            if exit_reason is None and bundle is not None and snapshot is not None:
                if _fundamental_invalidation(bundle):
                    exit_reason = "invalidated"
                elif _trim_conditions(snapshot, bundle, pos.sig_like, settings):
                    exit_reason = "trim"
                elif bundle.horizons[pos.trade.horizon].opportunity < 4.0:
                    exit_reason = "decay"
            if exit_reason is None and as_of > pos.expires_at:
                exit_reason = "horizon_elapsed"
            if exit_reason:
                _close_position(pos, panel, bench_by_market, as_of, exit_reason, settings, end)
                closed.append(pos.trade)
                del open_positions[key]

        # 2) fresh signals
        for inst in instruments:
            if inst.delisted_at is not None and inst.delisted_at <= as_of:
                continue
            try:
                snapshot = build_snapshot(
                    session, inst.id, as_of, settings, include_peers=include_peers
                )
            except SnapshotBuildError:
                continue
            ok, _reasons = universe_eligible(snapshot, settings)
            if not ok:
                continue
            results = run_all_engines(snapshot, settings)
            bundle = score_instrument(snapshot, results, settings, as_of)
            scans += 1
            for cand in generate_candidates(snapshot, bundle, settings):
                fam = cand.family.value
                if fam not in BUY_FAMILIES or cand.state_hint != "TRIGGERED":
                    continue
                key = (inst.id, fam, cand.horizon)
                if key in open_positions:
                    continue
                panel = panels[inst.id]
                spread = snapshot.liquidity.spread_estimate_bps
                cost = _cost_frac(spread, settings)
                fill = _fill(panel, as_of, "buy", cost)
                if fill is None:
                    continue
                entry_date, entry_px = fill
                regime = results.get("regime")
                trade = SimTrade(
                    instrument_id=inst.id,
                    ticker=inst.ticker,
                    sector=inst.sector,
                    family=fam,
                    horizon=cand.horizon,
                    signal_date=as_of,
                    entry_date=entry_date,
                    entry_price=entry_px,
                    costs_bps=cost * 2 * 10_000,  # round trip
                    opportunity=cand.scores.opportunity,
                    confidence=cand.scores.confidence,
                    risk=cand.scores.risk,
                    regime=(regime.details.get("regime_label", "unknown") if regime else "unknown"),
                    market_cap_base=snapshot.liquidity.market_cap_base,
                    details={"market": inst.market},
                )
                open_positions[key] = _OpenPosition(
                    trade,
                    cand.entry_plan.model_dump(mode="json"),
                    SIGNAL_TTL_DAYS[cand.horizon],
                )

    # Force-close whatever is still open at the end.
    for pos in open_positions.values():
        _close_position(
            pos, panels[pos.trade.instrument_id], bench_by_market, end, "backtest_end",
            settings, end,
        )
        closed.append(pos.trade)

    trades = closed
    in_sample = [t for t in trades if holdout_start is None or t.signal_date < holdout_start]
    holdout = [t for t in trades if holdout_start is not None and t.signal_date >= holdout_start]

    daily = _daily_curve(trades, panels, start, end)
    run.metrics = {
        "all": summarize(trades) | equity_curve_metrics(trades, daily),
        "in_sample": summarize(in_sample),
        "holdout": summarize(holdout) if holdout_start else {"note": "no holdout configured"},
        "scans": scans,
        "note": "sample sizes and CIs shown per bucket; buckets with n<20 are inconclusive",
    }
    run.by_bucket = bucketize(trades)
    run.calibration = calibration(trades)
    run.status = "ok"

    for t in trades:
        session.add(
            BacktestTrade(
                run_id=run.id,
                instrument_id=t.instrument_id,
                family=t.family,
                horizon=t.horizon,
                signal_date=t.signal_date,
                entry_date=t.entry_date,
                entry_price=t.entry_price,
                exit_date=t.exit_date,
                exit_price=t.exit_price,
                exit_reason=t.exit_reason,
                holding_days=t.holding_days,
                return_pct=t.return_pct,
                benchmark_return_pct=t.benchmark_return_pct,
                mae_pct=t.mae_pct,
                mfe_pct=t.mfe_pct,
                costs_bps=t.costs_bps,
                opportunity=t.opportunity,
                confidence=t.confidence,
                risk=t.risk,
                details={"regime": t.regime, "ticker": t.ticker, **t.details},
            )
        )
    session.flush()
    log.info("backtest %s: %d trades, %d scans", run.id, len(trades), scans)
    return run


def _close_position(
    pos: _OpenPosition,
    panel: pd.DataFrame,
    bench_by_market: dict[str, pd.Series],
    as_of: date,
    reason: str,
    settings: Settings,
    hard_end: date,
) -> None:
    t = pos.trade
    cost = _cost_frac(None, settings)
    if reason == "delisted":
        last = panel.iloc[-1]
        exit_date, exit_px = panel.index[-1].date(), float(last["adj_close"]) * (1 - cost)
    else:
        fill = _fill(panel, as_of, "sell", cost)
        if fill is None:
            last = panel.iloc[-1]
            exit_date, exit_px = panel.index[-1].date(), float(last["adj_close"]) * (1 - cost)
            reason = f"{reason}(last_bar)"
        else:
            exit_date, exit_px = fill
    t.exit_date, t.exit_price, t.exit_reason = exit_date, exit_px, reason
    if t.entry_price and t.entry_price > 0:
        t.return_pct = round((exit_px / t.entry_price - 1) * 100, 3)
    window = panel.loc[
        (panel.index >= pd.Timestamp(t.entry_date)) & (panel.index <= pd.Timestamp(exit_date))
    ]
    if not window.empty and t.entry_price:
        t.mae_pct = round((float(window["adj_close"].min()) / t.entry_price - 1) * 100, 2)
        t.mfe_pct = round((float(window["adj_close"].max()) / t.entry_price - 1) * 100, 2)
    t.holding_days = int(np.busday_count(t.entry_date, exit_date)) if t.entry_date else None
    bench = None
    for series in bench_by_market.values():
        bench = series
        break
    # match benchmark by the trade's market when available
    if t.details.get("market") in bench_by_market:
        bench = bench_by_market[t.details["market"]]
    if bench is not None and t.entry_date:
        b = bench.loc[
            (bench.index >= pd.Timestamp(t.entry_date)) & (bench.index <= pd.Timestamp(exit_date))
        ]
        if len(b) >= 2 and float(b.iloc[0]) > 0:
            t.benchmark_return_pct = round((float(b.iloc[-1]) / float(b.iloc[0]) - 1) * 100, 3)


def _daily_curve(
    trades: list, panels: dict[int, pd.DataFrame], start: date, end: date
) -> pd.Series:
    """Equal-weight daily return series across open trades (cash earns 0)."""
    idx = pd.bdate_range(start, end)
    rets = pd.DataFrame(index=idx)
    for i, t in enumerate(trades):
        if not t.closed or not t.entry_date:
            continue
        panel = panels[t.instrument_id]
        series = panel["adj_close"].pct_change()
        window = series.loc[
            (series.index > pd.Timestamp(t.entry_date)) & (series.index <= pd.Timestamp(t.exit_date))
        ]
        if not window.empty:
            rets[f"t{i}"] = window
    if rets.empty:
        return pd.Series(dtype=float)
    return rets.mean(axis=1, skipna=True).fillna(0.0)
