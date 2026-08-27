"""Point-in-time snapshot builder — the ONLY read path research engines see.

All look-ahead prevention is concentrated here and tested in
``tests/data/test_snapshot_pit.py``:

* price bars:            bar_date <= as_of
* corporate actions:     applied to the adjusted series only when
                         ex_date <= as_of (a later split never rewrites
                         what an earlier snapshot saw)
* fundamentals:          published_at <= as_of (restatements become visible
                         only from their own publication date)
* estimates/targets:     snapshot as_of <= snapshot date
* news/short-interest/insiders: publication or filing time <= as_of
* catalysts:             visible if announced (published_at <= as_of);
                         resolution fields are masked until outcome_date
* macro:                 published_at <= as_of
* FX:                    latest rate_date <= as_of
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from vigil.config import Settings, get_settings
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
    SharesOutstandingObs,
    ShortInterestObs,
    TargetSnap,
)
from vigil.schemas.core import (
    CatalystRecord,
    CorporateActionRecord,
    DataQualityFlags,
    EstimateRecord,
    FundamentalRecord,
    InsiderRecord,
    InstrumentInfo,
    InstrumentSnapshot,
    LiquidityStats,
    NewsRecord,
    PeerMetrics,
    ShortInterestRecord,
    SourceRef,
    TargetRecord,
)

NEWS_WINDOW_DAYS = 270
OWNERSHIP_WINDOW_DAYS = 400
MAX_PEERS = 8


def _eod(as_of: date) -> datetime:
    """Snapshot cutoff: end of the as_of calendar day, UTC-naive."""
    return datetime.combine(as_of, time(23, 59, 59))


# ---------------------------------------------------------------------------
# Prices and adjustment
# ---------------------------------------------------------------------------


def load_price_frame(session: Session, instrument_id: int, as_of: date) -> pd.DataFrame:
    """Daily OHLCV up to as_of with point-in-time adjusted close/volume."""
    rows = session.execute(
        select(PriceBar)
        .where(PriceBar.instrument_id == instrument_id, PriceBar.bar_date <= as_of)
        .order_by(PriceBar.bar_date)
    ).scalars().all()
    if not rows:
        return pd.DataFrame(
            columns=["open", "high", "low", "close", "adj_close", "volume"]
        )
    df = pd.DataFrame(
        {
            "open": [r.open for r in rows],
            "high": [r.high for r in rows],
            "low": [r.low for r in rows],
            "close": [r.close for r in rows],
            "volume": [r.volume for r in rows],
        },
        index=pd.DatetimeIndex([pd.Timestamp(r.bar_date) for r in rows], name="date"),
    )
    actions = session.execute(
        select(CorporateAction)
        .where(
            CorporateAction.instrument_id == instrument_id,
            CorporateAction.ex_date <= as_of,
        )
        .order_by(CorporateAction.ex_date)
    ).scalars().all()

    # Split adjustment: bars strictly before the ex_date divide prices by the
    # cumulative factor (and multiply volume). Dividend adjustment applies the
    # standard proportional factor to adj_close only.
    adj_factor = pd.Series(1.0, index=df.index)  # price divisor per bar
    div_factor = pd.Series(1.0, index=df.index)
    for act in actions:
        ex = pd.Timestamp(act.ex_date)
        if act.kind == "split" and act.factor and act.factor > 0:
            mask = df.index < ex
            adj_factor.loc[mask] *= act.factor
        elif act.kind == "dividend" and act.amount and act.amount > 0:
            mask = df.index < ex
            prior = df.loc[mask, "close"]
            if prior.empty:
                continue
            # Dividend amount is in raw (unadjusted) currency; the standard
            # proportional factor scales all earlier adjusted closes down.
            raw_last = float(prior.iloc[-1])
            if raw_last > 0:
                div_factor.loc[mask] *= max(0.0, 1.0 - act.amount / raw_last)
    df["adj_close"] = df["close"] / adj_factor * div_factor
    df["adj_open"] = df["open"] / adj_factor * div_factor
    df["volume"] = df["volume"] * adj_factor
    return df


# ---------------------------------------------------------------------------
# Fundamentals
# ---------------------------------------------------------------------------

_FUND_FIELDS = [
    "revenue", "gross_profit", "operating_income", "net_income", "eps_diluted",
    "shares_diluted", "interest_expense", "operating_cash_flow", "capex",
    "dividends_paid", "buybacks", "stock_based_comp", "total_assets",
    "total_equity", "total_debt", "cash_and_equivalents", "current_assets",
    "current_liabilities", "receivables", "inventory", "goodwill_intangibles",
    "debt_due_within_1y", "largest_customer_pct", "adjusted_profit_exclusions",
]


def load_fundamentals(
    session: Session, instrument_id: int, as_of: date
) -> tuple[FundamentalRecord, ...]:
    rows = session.execute(
        select(FundamentalReport)
        .where(
            FundamentalReport.instrument_id == instrument_id,
            FundamentalReport.published_at <= _eod(as_of),
        )
        .order_by(FundamentalReport.period_end, FundamentalReport.published_at)
    ).scalars().all()
    records = []
    for r in rows:
        payload = r.payload or {}
        kwargs = {k: payload.get(k) for k in _FUND_FIELDS}
        records.append(
            FundamentalRecord(
                period_end=r.period_end,
                period_type=r.period_type,  # type: ignore[arg-type]
                published_at=r.published_at,
                is_restatement=r.is_restatement,
                restates_period_end=r.restates_period_end,
                currency=r.currency,
                auditor=payload.get("auditor"),
                sector_metrics=payload.get("sector_metrics", {}),
                source=SourceRef(
                    provider=r.provider,
                    source_type="fundamental",
                    reference=r.source_reference or f"fundamental_reports/{r.id}",
                    published_at=r.published_at,
                    retrieved_at=r.ingested_at,
                    freshness_days=float((as_of - r.published_at.date()).days),
                ),
                **kwargs,
            )
        )
    return tuple(records)


# ---------------------------------------------------------------------------
# Estimates / targets
# ---------------------------------------------------------------------------


def load_estimates(
    session: Session, instrument_id: int, as_of: date
) -> tuple[EstimateRecord, ...]:
    rows = session.execute(
        select(EstimateSnap)
        .where(EstimateSnap.instrument_id == instrument_id, EstimateSnap.as_of <= as_of)
        .order_by(EstimateSnap.as_of)
    ).scalars().all()
    latest: dict[tuple[str, str], EstimateSnap] = {}
    for r in rows:
        latest[(r.metric, r.fiscal_label)] = r  # ordered by as_of, so last wins
    records = []
    for r in latest.values():
        if r.period_end < as_of - timedelta(days=400):
            continue  # long-dead fiscal periods add noise
        records.append(
            EstimateRecord(
                as_of=r.as_of,
                metric=r.metric,  # type: ignore[arg-type]
                fiscal_label=r.fiscal_label,
                period_end=r.period_end,
                mean=r.mean,
                high=r.high,
                low=r.low,
                analyst_count=r.analyst_count,
                mean_30d_ago=r.mean_30d_ago,
                mean_90d_ago=r.mean_90d_ago,
                up_revisions_30d=r.up_revisions_30d,
                down_revisions_30d=r.down_revisions_30d,
                source=SourceRef(
                    provider=r.provider,
                    source_type="estimate",
                    reference=f"estimate_snaps/{r.id}",
                    published_at=datetime.combine(r.as_of, time(0, 0)),
                    freshness_days=float((as_of - r.as_of).days),
                ),
            )
        )
    records.sort(key=lambda e: (e.period_end, e.metric))
    return tuple(records)


def load_target(session: Session, instrument_id: int, as_of: date) -> TargetRecord | None:
    r = session.execute(
        select(TargetSnap)
        .where(TargetSnap.instrument_id == instrument_id, TargetSnap.as_of <= as_of)
        .order_by(TargetSnap.as_of.desc())
        .limit(1)
    ).scalar_one_or_none()
    if r is None:
        return None
    return TargetRecord(
        as_of=r.as_of,
        currency=r.currency,
        mean=r.mean,
        high=r.high,
        low=r.low,
        std=r.std,
        analyst_count=r.analyst_count,
        median_age_days=r.median_age_days,
        mean_30d_ago=r.mean_30d_ago,
        source=SourceRef(
            provider=r.provider,
            source_type="target",
            reference=f"target_snaps/{r.id}",
            published_at=datetime.combine(r.as_of, time(0, 0)),
            freshness_days=float((as_of - r.as_of).days),
        ),
    )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def load_news(session: Session, instrument_id: int, as_of: date) -> tuple[NewsRecord, ...]:
    cutoff_low = _eod(as_of) - timedelta(days=NEWS_WINDOW_DAYS)
    rows = session.execute(
        select(NewsItem)
        .where(
            NewsItem.instrument_id == instrument_id,
            NewsItem.published_at <= _eod(as_of),
            NewsItem.published_at >= cutoff_low,
        )
        .order_by(NewsItem.published_at)
    ).scalars().all()
    return tuple(
        NewsRecord(
            record_id=str(r.id),
            published_at=r.published_at,
            headline=r.headline,
            summary=r.summary,
            source_name=r.source_name,
            source_type=r.source_type,  # type: ignore[arg-type]
            url=r.url,
            sentiment=r.sentiment,
            novelty=r.novelty,
            source=SourceRef(
                provider=r.provider,
                source_type="news",
                reference=r.url or f"news_items/{r.id}",
                published_at=r.published_at,
                retrieved_at=r.ingested_at,
                freshness_days=float((_eod(as_of) - r.published_at).days),
            ),
        )
        for r in rows
    )


def load_catalysts(
    session: Session, instrument_id: int, as_of: date
) -> tuple[CatalystRecord, ...]:
    rows = session.execute(
        select(Catalyst)
        .where(Catalyst.instrument_id == instrument_id)
        .order_by(Catalyst.expected_date)
    ).scalars().all()
    records = []
    for r in rows:
        announced = r.published_at is None or r.published_at <= _eod(as_of)
        if not announced:
            continue
        # Mask resolution the snapshot must not know about yet.
        resolved_visible = r.resolved and r.outcome_date is not None and r.outcome_date <= as_of
        if r.expected_date < as_of - timedelta(days=180) and not resolved_visible:
            continue  # stale unresolved noise
        records.append(
            CatalystRecord(
                record_id=str(r.id),
                kind=r.kind,  # type: ignore[arg-type]
                expected_date=r.expected_date,
                date_confirmed=r.date_confirmed,
                description=r.description,
                binary=r.binary,
                published_at=r.published_at,
                resolved=resolved_visible,
                outcome=r.outcome if resolved_visible else None,
                outcome_date=r.outcome_date if resolved_visible else None,
                source=SourceRef(
                    provider=r.provider,
                    source_type="news",
                    reference=r.url or f"catalysts/{r.id}",
                    published_at=r.published_at,
                    freshness_days=(
                        float((_eod(as_of) - r.published_at).days) if r.published_at else None
                    ),
                ),
            )
        )
    return tuple(records)


def load_short_interest(
    session: Session, instrument_id: int, as_of: date
) -> tuple[ShortInterestRecord, ...]:
    rows = session.execute(
        select(ShortInterestObs)
        .where(
            ShortInterestObs.instrument_id == instrument_id,
            ShortInterestObs.published_at <= _eod(as_of),
            ShortInterestObs.as_of >= as_of - timedelta(days=OWNERSHIP_WINDOW_DAYS),
        )
        .order_by(ShortInterestObs.as_of)
    ).scalars().all()
    return tuple(
        ShortInterestRecord(
            as_of=r.as_of,
            shares_short=r.shares_short,
            pct_float=r.pct_float,
            days_to_cover=r.days_to_cover,
            source=SourceRef(
                provider=r.provider,
                source_type="short_interest",
                reference=f"short_interest_obs/{r.id}",
                published_at=r.published_at,
                freshness_days=float((as_of - r.as_of).days),
            ),
        )
        for r in rows
    )


def load_insiders(
    session: Session, instrument_id: int, as_of: date
) -> tuple[InsiderRecord, ...]:
    rows = session.execute(
        select(InsiderTx)
        .where(
            InsiderTx.instrument_id == instrument_id,
            InsiderTx.filed_at <= _eod(as_of),
            InsiderTx.filed_at >= _eod(as_of) - timedelta(days=OWNERSHIP_WINDOW_DAYS),
        )
        .order_by(InsiderTx.filed_at)
    ).scalars().all()
    return tuple(
        InsiderRecord(
            filed_at=r.filed_at,
            transaction_date=r.transaction_date,
            insider_name=r.insider_name,
            insider_role=r.insider_role,
            kind=r.kind,  # type: ignore[arg-type]
            shares=r.shares,
            value=r.value,
            source=SourceRef(
                provider=r.provider,
                source_type="insider",
                reference=r.url or f"insider_txs/{r.id}",
                published_at=r.filed_at,
                freshness_days=float((_eod(as_of) - r.filed_at).days),
            ),
        )
        for r in rows
    )


def load_actions_records(
    session: Session, instrument_id: int, as_of: date
) -> tuple[CorporateActionRecord, ...]:
    rows = session.execute(
        select(CorporateAction)
        .where(
            CorporateAction.instrument_id == instrument_id,
            CorporateAction.ex_date <= as_of,
        )
        .order_by(CorporateAction.ex_date)
    ).scalars().all()
    return tuple(
        CorporateActionRecord(
            kind=r.kind,  # type: ignore[arg-type]
            ex_date=r.ex_date,
            factor=r.factor,
            amount=r.amount,
            detail=r.detail,
            source=SourceRef(
                provider=r.provider,
                source_type="corporate_action",
                reference=f"corporate_actions/{r.id}",
                published_at=r.published_at,
            ),
        )
        for r in rows
    )


# ---------------------------------------------------------------------------
# Macro / FX / benchmark
# ---------------------------------------------------------------------------


def load_macro(session: Session, as_of: date) -> dict[str, pd.Series]:
    rows = session.execute(
        select(MacroObservation)
        .where(MacroObservation.published_at <= _eod(as_of))
        .order_by(MacroObservation.series_id, MacroObservation.obs_date)
    ).scalars().all()
    out: dict[str, list[tuple[date, float]]] = {}
    for r in rows:
        out.setdefault(r.series_id, []).append((r.obs_date, r.value))
    return {
        sid: pd.Series(
            [v for _, v in obs],
            index=pd.DatetimeIndex([pd.Timestamp(d) for d, _ in obs]),
            name=sid,
        )
        for sid, obs in out.items()
    }


def fx_to_base(
    session: Session, currency: str, base: str, as_of: date
) -> tuple[float, date | None]:
    if currency == base:
        return 1.0, as_of
    row = session.execute(
        select(FxRate)
        .where(
            FxRate.base_ccy == currency,
            FxRate.quote_ccy == base,
            FxRate.rate_date <= as_of,
        )
        .order_by(FxRate.rate_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is not None:
        return row.rate, row.rate_date
    # Try the inverse quotation.
    row = session.execute(
        select(FxRate)
        .where(
            FxRate.base_ccy == base,
            FxRate.quote_ccy == currency,
            FxRate.rate_date <= as_of,
        )
        .order_by(FxRate.rate_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is not None and row.rate > 0:
        return 1.0 / row.rate, row.rate_date
    return 1.0, None  # flagged in quality warnings by the caller


def _index_series(
    session: Session, market: str, sector: str, as_of: date
) -> pd.Series | None:
    """Benchmark series for a market/sector. More than one matching index
    row can exist (editing universe.yml adds instruments but never deletes
    old ones — e.g. ^SPX swapped for SPY leaves both); pick the one with
    the longest usable price history rather than assuming exactly one."""
    q = select(Instrument).where(
        Instrument.security_type == "index",
        Instrument.market == market,
        Instrument.sector == sector,
    )
    best: pd.Series | None = None
    for idx in session.execute(q).scalars():
        df = load_price_frame(session, idx.id, as_of)
        if df.empty:
            continue
        series = df["adj_close"].rename(idx.ticker)
        if best is None or len(series) > len(best):
            best = series
    return best


# ---------------------------------------------------------------------------
# Peers
# ---------------------------------------------------------------------------


def _basic_peer_metrics(
    session: Session, inst: Instrument, as_of: date
) -> PeerMetrics | None:
    df = load_price_frame(session, inst.id, as_of)
    if df.empty:
        return None
    price = float(df["close"].iloc[-1])
    shares = latest_shares(session, inst.id, as_of)
    funds = load_fundamentals(session, inst.id, as_of)
    qs = [f for f in _fold(funds) if f.period_type == "Q"]
    metrics: dict[str, float] = {}
    if shares and price > 0:
        mcap = price * shares
        metrics["market_cap"] = mcap
        if len(qs) >= 4:
            last4 = qs[-4:]
            ni = _sum_of(last4, "net_income")
            rev = _sum_of(last4, "revenue")
            fcf = _sum_fcf(last4)
            gp = _sum_of(last4, "gross_profit")
            latest = qs[-1]
            debt = latest.total_debt or 0.0
            cash = latest.cash_and_equivalents or 0.0
            ev = mcap + debt - cash
            op = _sum_of(last4, "operating_income")
            if ni and ni > 0:
                metrics["pe_ttm"] = mcap / ni
            if rev and rev > 0:
                metrics["ev_sales"] = ev / rev
                if gp is not None:
                    metrics["gross_margin"] = gp / rev
            if op and op > 0:
                metrics["ev_ebit"] = ev / op
            if fcf is not None and mcap > 0:
                metrics["fcf_yield"] = fcf / mcap
            if latest.total_equity and latest.total_equity > 0:
                metrics["pb"] = mcap / latest.total_equity
            if len(qs) >= 8:
                prev4rev = _sum_of(qs[-8:-4], "revenue")
                if rev and prev4rev:
                    metrics["revenue_growth_ttm"] = rev / prev4rev - 1.0
            if op and op > 0 and latest.total_equity is not None:
                nd = debt - cash
                metrics["net_debt_ebit"] = nd / op
    if not metrics:
        return None
    return PeerMetrics(
        instrument_id=inst.id,
        ticker=inst.ticker,
        name=inst.name,
        sector=inst.sector,
        industry=inst.industry,
        metrics={k: round(v, 6) for k, v in metrics.items()},
    )


def _fold(records: tuple[FundamentalRecord, ...]) -> list[FundamentalRecord]:
    by_period: dict[tuple[str, date], FundamentalRecord] = {}
    for rec in records:
        key = (rec.period_type, rec.restates_period_end or rec.period_end)
        cur = by_period.get(key)
        if cur is None or rec.published_at > cur.published_at:
            by_period[key] = rec
    return [by_period[k] for k in sorted(by_period, key=lambda t: (t[0], t[1]))]


def _sum_of(records: list[FundamentalRecord], fieldname: str) -> float | None:
    vals = [getattr(r, fieldname) for r in records]
    if any(v is None for v in vals):
        return None
    return float(sum(vals))


def _sum_fcf(records: list[FundamentalRecord]) -> float | None:
    vals = [r.free_cash_flow for r in records]
    if any(v is None for v in vals):
        return None
    return float(sum(v for v in vals if v is not None))


def latest_shares(session: Session, instrument_id: int, as_of: date) -> float | None:
    row = session.execute(
        select(SharesOutstandingObs)
        .where(
            SharesOutstandingObs.instrument_id == instrument_id,
            SharesOutstandingObs.published_at <= _eod(as_of),
        )
        .order_by(SharesOutstandingObs.published_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row.shares if row else None


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


@dataclass
class SnapshotBuildError(Exception):
    instrument_id: int
    reason: str

    def __str__(self) -> str:
        return f"snapshot({self.instrument_id}): {self.reason}"


def build_snapshot(
    session: Session,
    instrument_id: int,
    as_of: date,
    settings: Settings | None = None,
    include_peers: bool = True,
) -> InstrumentSnapshot:
    settings = settings or get_settings()
    inst = session.get(Instrument, instrument_id)
    if inst is None:
        raise SnapshotBuildError(instrument_id, "unknown instrument")

    prices = load_price_frame(session, instrument_id, as_of)
    if prices.empty:
        raise SnapshotBuildError(instrument_id, f"no price history on or before {as_of}")

    shares = latest_shares(session, instrument_id, as_of)
    info = InstrumentInfo(
        instrument_id=inst.id,
        ticker=inst.ticker,
        exchange=inst.exchange,
        market=inst.market,
        name=inst.name,
        sector=inst.sector,
        industry=inst.industry,
        currency=inst.currency,
        security_type=inst.security_type,
        is_shell=inst.is_shell,
        is_active=inst.delisted_at is None or inst.delisted_at > as_of,
        listed_at=inst.listed_at,
        delisted_at=inst.delisted_at if inst.delisted_at and inst.delisted_at <= as_of else None,
        shares_outstanding=shares,
    )

    benchmark = _index_series(session, inst.market, "", as_of)
    if benchmark is None:
        benchmark = pd.Series(dtype=float)
    sector_index = _index_series(session, inst.market, inst.sector, as_of) if inst.sector else None

    rate, rate_date = fx_to_base(session, inst.currency, settings.base_currency, as_of)

    fundamentals = load_fundamentals(session, instrument_id, as_of)
    estimates = load_estimates(session, instrument_id, as_of)
    target = load_target(session, instrument_id, as_of)
    news = load_news(session, instrument_id, as_of)
    catalysts = load_catalysts(session, instrument_id, as_of)
    shorts = load_short_interest(session, instrument_id, as_of)
    insiders = load_insiders(session, instrument_id, as_of)
    actions = load_actions_records(session, instrument_id, as_of)
    macro = load_macro(session, as_of)

    peers: tuple[PeerMetrics, ...] = ()
    if include_peers and inst.sector:
        peer_rows = session.execute(
            select(Instrument).where(
                Instrument.sector == inst.sector,
                Instrument.id != inst.id,
                Instrument.security_type == "common",
            )
        ).scalars().all()
        collected = []
        for peer in peer_rows[: MAX_PEERS * 2]:
            pm = _basic_peer_metrics(session, peer, as_of)
            if pm is not None:
                collected.append(pm)
            if len(collected) >= MAX_PEERS:
                break
        peers = tuple(collected)

    liquidity = _liquidity_stats(prices, info, rate, as_of, settings)
    quality = _quality_flags(
        as_of, prices, fundamentals, estimates, news, target, rate_date, liquidity
    )

    return InstrumentSnapshot(
        as_of=as_of,
        info=info,
        prices=prices,
        benchmark=benchmark,
        sector_index=sector_index,
        fx_to_base=rate,
        fx_as_of=rate_date,
        fundamentals=fundamentals,
        estimates=estimates,
        target=target,
        news=news,
        catalysts=catalysts,
        short_interest=shorts,
        insiders=insiders,
        corporate_actions=actions,
        peers=peers,
        macro=macro,
        liquidity=liquidity,
        quality=quality,
    )


def _liquidity_stats(
    prices: pd.DataFrame,
    info: InstrumentInfo,
    fx_rate: float,
    as_of: date,
    settings: Settings,
) -> LiquidityStats:
    window = settings.universe.liquidity_window_days
    tail = prices.iloc[-window:]
    traded = (tail["close"] * tail["volume"]).median() if not tail.empty else None
    last_bar = prices.index[-1].date()
    staleness = int(np.busday_count(last_bar, as_of))
    mcap_local = None
    if info.shares_outstanding and not prices.empty:
        mcap_local = float(prices["close"].iloc[-1]) * info.shares_outstanding
    # Rough spread heuristic by traded-value band (documented in FORMULAS.md).
    spread = None
    if traded is not None and traded > 0:
        traded_base = float(traded) * fx_rate
        if traded_base > 20e6:
            spread = 5.0
        elif traded_base > 5e6:
            spread = 12.0
        elif traded_base > 1e6:
            spread = 25.0
        else:
            spread = 60.0
    return LiquidityStats(
        market_cap_local=mcap_local,
        market_cap_base=mcap_local * fx_rate if mcap_local is not None else None,
        median_daily_traded_value_local=float(traded) if traded is not None else None,
        median_daily_traded_value_base=(
            float(traded) * fx_rate if traded is not None else None
        ),
        spread_estimate_bps=spread,
        price_staleness_days=max(0, staleness),
    )


def _quality_flags(
    as_of: date,
    prices: pd.DataFrame,
    fundamentals: tuple[FundamentalRecord, ...],
    estimates: tuple[EstimateRecord, ...],
    news: tuple[NewsRecord, ...],
    target: TargetRecord | None,
    fx_date: date | None,
    liquidity: LiquidityStats,
) -> DataQualityFlags:
    warnings: list[str] = []
    missing: list[str] = []
    parts: list[float] = []

    parts.append(1.0 if len(prices) >= 252 else len(prices) / 252)
    if liquidity.price_staleness_days > 3:
        warnings.append(f"price data stale by {liquidity.price_staleness_days} trading days")

    q_count = sum(1 for f in fundamentals if f.period_type == "Q")
    fund_age = None
    if fundamentals:
        latest_pub = max(f.published_at for f in fundamentals)
        fund_age = (as_of - latest_pub.date()).days
        parts.append(min(1.0, q_count / 8))
        if fund_age > 150:
            warnings.append(f"latest fundamentals published {fund_age} days ago")
    else:
        parts.append(0.0)
        missing.append("fundamentals")

    if estimates:
        parts.append(1.0)
        freshest_age = min((as_of - e.as_of).days for e in estimates)
        if freshest_age > 45:
            warnings.append(f"estimates snapshot is {freshest_age} days old")
    else:
        parts.append(0.0)
        missing.append("analyst_estimates")

    parts.append(1.0 if news else 0.5)
    if not news:
        missing.append("news")
    if target is None:
        missing.append("analyst_targets")
    if fx_date is None:
        warnings.append("no FX rate found; base-currency figures assume parity")
    restated = [f for f in fundamentals if f.is_restatement]
    if restated:
        latest_restate = max(f.published_at for f in restated)
        if (as_of - latest_restate.date()).days <= 180:
            warnings.append("recent financial restatement in the last 6 months")

    return DataQualityFlags(
        completeness=round(float(np.mean(parts)), 3) if parts else 0.0,
        price_staleness_days=liquidity.price_staleness_days,
        latest_fundamental_age_days=fund_age,
        estimates_available=bool(estimates),
        news_available=bool(news),
        warnings=warnings,
        missing=missing,
    )
