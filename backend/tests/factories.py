"""In-memory snapshot factory for engine unit tests (no database needed).

Build price paths and fundamentals with explicit shapes, then assert on
engine behaviour. Deterministic: seeded RNG per call.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from vigil.schemas.core import (
    CatalystRecord,
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

AS_OF = date(2026, 8, 25)


def _src(kind: str = "derived", published: datetime | None = None) -> SourceRef:
    return SourceRef(
        provider="test",
        source_type=kind,  # type: ignore[arg-type]
        reference=f"test://{kind}",
        published_at=published,
        freshness_days=1.0,
    )


def price_frame(
    days: int = 700,
    start_price: float = 100.0,
    daily_drift: float = 0.0004,
    daily_vol: float = 0.015,
    seed: int = 7,
    end: date = AS_OF,
    shape: list[tuple[float, float, float]] | None = None,
    volume: float = 2e6,
) -> pd.DataFrame:
    """Geometric path. ``shape`` overrides drift as a list of
    (start_frac, end_frac, annual_drift) segments."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=days)
    drift = np.full(days, daily_drift)
    if shape:
        for lo, hi, annual in shape:
            drift[int(lo * days) : int(hi * days)] = annual / 252.0
    rets = drift + rng.standard_normal(days) * daily_vol
    close = start_price * np.exp(np.cumsum(rets))
    close = close / close[0] * start_price
    opens = np.empty(days)
    opens[0] = close[0]
    opens[1:] = close[:-1]
    high = np.maximum(opens, close) * (1 + np.abs(rng.standard_normal(days)) * daily_vol * 0.4)
    low = np.minimum(opens, close) * (1 - np.abs(rng.standard_normal(days)) * daily_vol * 0.4)
    vol = volume * np.exp(rng.standard_normal(days) * 0.3)
    df = pd.DataFrame(
        {"open": opens, "high": high, "low": low, "close": close,
         "adj_close": close, "adj_open": opens, "volume": vol},
        index=idx,
    )
    return df


def quarterly_fundamentals(
    quarters: int = 12,
    revenue0: float = 1000e6,
    revenue_growth_q: float = 0.02,
    gross_margin: float = 0.5,
    op_margin: float = 0.2,
    net_margin: float = 0.15,
    cash_conversion: float = 1.1,
    capex_pct: float = 0.05,
    shares: float = 100e6,
    debt: float = 500e6,
    cash: float = 300e6,
    equity0: float = 2000e6,
    end: date = AS_OF,
    publication_lag_days: int = 45,
    overrides: dict[int, dict] | None = None,
) -> tuple[FundamentalRecord, ...]:
    """Simple coherent quarterly series ending with a period published
    before ``end``. ``overrides[i]`` patches fields of quarter i (0-oldest)."""
    # Work backwards to find the last period publishable before `end`.
    last_pe = pd.Timestamp(end) - pd.offsets.QuarterEnd(1)
    while last_pe.date() + timedelta(days=publication_lag_days) > end:
        last_pe -= pd.offsets.QuarterEnd(1)
    period_ends = [
        (last_pe - pd.offsets.QuarterEnd(quarters - 1 - i)).date() for i in range(quarters)
    ]
    records = []
    rev = revenue0
    equity = equity0
    for i, pe in enumerate(period_ends):
        rev = rev * (1 + revenue_growth_q)
        ni = rev * net_margin
        ocf = ni * cash_conversion
        capex = rev * capex_pct
        fields = dict(
            revenue=rev,
            gross_profit=rev * gross_margin,
            operating_income=rev * op_margin,
            net_income=ni,
            eps_diluted=ni / shares,
            shares_diluted=shares,
            interest_expense=debt * 0.05 / 4,
            operating_cash_flow=ocf,
            capex=capex,
            dividends_paid=ni * 0.2,
            buybacks=0.0,
            stock_based_comp=rev * 0.02,
            total_assets=equity + debt + rev * 0.8,
            total_equity=equity,
            total_debt=debt,
            cash_and_equivalents=cash,
            current_assets=cash + rev * 0.6,
            current_liabilities=rev * 0.5,
            receivables=rev * 0.55,
            inventory=rev * 0.4,
            goodwill_intangibles=equity * 0.2,
            debt_due_within_1y=debt * 0.1,
        )
        if overrides and i in overrides:
            fields.update(overrides[i])
        equity += fields["net_income"]
        records.append(
            FundamentalRecord(
                period_end=pe,
                period_type="Q",
                published_at=datetime.combine(pe + timedelta(days=publication_lag_days), time(12)),
                currency="USD",
                source=_src("fundamental", datetime.combine(pe, time(12))),
                **fields,
            )
        )
    return tuple(records)


def make_snapshot(
    ticker: str = "TEST",
    sector: str = "Technology",
    industry: str = "Software",
    market: str = "US",
    currency: str = "USD",
    as_of: date = AS_OF,
    prices: pd.DataFrame | None = None,
    benchmark: pd.Series | None = None,
    sector_index: pd.Series | None = None,
    fundamentals: tuple[FundamentalRecord, ...] = (),
    estimates: tuple[EstimateRecord, ...] = (),
    target: TargetRecord | None = None,
    news: tuple[NewsRecord, ...] = (),
    catalysts: tuple[CatalystRecord, ...] = (),
    short_interest: tuple[ShortInterestRecord, ...] = (),
    insiders: tuple[InsiderRecord, ...] = (),
    peers: tuple[PeerMetrics, ...] = (),
    macro: dict[str, pd.Series] | None = None,
    shares: float = 100e6,
    liquidity: LiquidityStats | None = None,
    quality: DataQualityFlags | None = None,
) -> InstrumentSnapshot:
    prices = prices if prices is not None else price_frame(end=as_of)
    if benchmark is None:
        benchmark = price_frame(days=len(prices), seed=1, daily_vol=0.01, end=as_of)["adj_close"]
    if liquidity is None:
        last = float(prices["close"].iloc[-1])
        traded = float((prices["close"] * prices["volume"]).iloc[-63:].median())
        liquidity = LiquidityStats(
            market_cap_local=last * shares,
            market_cap_base=last * shares * 0.78,
            median_daily_traded_value_local=traded,
            median_daily_traded_value_base=traded * 0.78,
            spread_estimate_bps=10.0,
            price_staleness_days=0,
        )
    return InstrumentSnapshot(
        as_of=as_of,
        info=InstrumentInfo(
            instrument_id=1, ticker=ticker, exchange="NYSE", market=market,
            name=f"{ticker} Corp", sector=sector, industry=industry,
            currency=currency, shares_outstanding=shares,
        ),
        prices=prices,
        benchmark=benchmark,
        sector_index=sector_index,
        fx_to_base=0.78 if currency == "USD" else 1.0,
        fx_as_of=as_of,
        fundamentals=fundamentals,
        estimates=estimates,
        target=target,
        news=news,
        catalysts=catalysts,
        short_interest=short_interest,
        insiders=insiders,
        corporate_actions=(),
        peers=peers,
        macro=macro or {},
        liquidity=liquidity,
        quality=quality or DataQualityFlags(),
    )


def estimate(
    metric: str = "eps",
    fiscal_label: str = "FY2026",
    mean: float = 5.0,
    mean_30d_ago: float | None = 4.8,
    mean_90d_ago: float | None = 4.6,
    analyst_count: int = 12,
    up: int = 6,
    down: int = 1,
    as_of: date = AS_OF - timedelta(days=5),
) -> EstimateRecord:
    return EstimateRecord(
        as_of=as_of,
        metric=metric,  # type: ignore[arg-type]
        fiscal_label=fiscal_label,
        period_end=date(int(fiscal_label[2:]), 12, 31),
        mean=mean,
        high=mean * 1.1,
        low=mean * 0.9,
        analyst_count=analyst_count,
        mean_30d_ago=mean_30d_ago,
        mean_90d_ago=mean_90d_ago,
        up_revisions_30d=up,
        down_revisions_30d=down,
        source=_src("estimate"),
    )


def news_item(
    days_ago: int,
    sentiment: float,
    source_type: str = "factual_event",
    headline: str = "Company update",
    novelty: float = 1.0,
    as_of: date = AS_OF,
) -> NewsRecord:
    published = datetime.combine(as_of - timedelta(days=days_ago), time(9))
    return NewsRecord(
        record_id=f"n{days_ago}-{source_type}-{abs(hash(headline)) % 1000}",
        published_at=published,
        headline=headline,
        summary="",
        source_name="Test Wire",
        source_type=source_type,  # type: ignore[arg-type]
        sentiment=sentiment,
        novelty=novelty,
        source=_src("news", published),
    )


def catalyst(
    days_ahead: int,
    kind: str = "earnings",
    binary: bool = False,
    confirmed: bool = True,
    description: str = "Quarterly results",
    as_of: date = AS_OF,
) -> CatalystRecord:
    return CatalystRecord(
        record_id=f"c{kind}{days_ahead}",
        kind=kind,  # type: ignore[arg-type]
        expected_date=as_of + timedelta(days=days_ahead),
        date_confirmed=confirmed,
        description=description,
        binary=binary,
        published_at=datetime.combine(as_of - timedelta(days=30), time(9)),
        source=_src("news"),
    )
