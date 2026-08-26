"""Synthetic provider: serves the deterministic generated world through the
standard provider protocols, so the entire pipeline (ingest, lineage,
snapshots, engines, alerts, backtests) is exercised exactly as it would be
with a real vendor."""

from __future__ import annotations

from datetime import date

from vigil.providers import base as p
from vigil.providers.base import CapabilityUnavailable
from vigil.providers.synthetic.generator import (
    PROVIDER,
    StockWorld,
    build_fx,
    build_macro,
    market_index_closes,
    sector_index_closes,
)
from vigil.providers.synthetic.universe import SEED, SPECS, WORLD_NOW


class SyntheticProvider:
    name = PROVIDER

    def __init__(self) -> None:
        self._worlds: dict[str, StockWorld] = {}
        self._spec_by_ticker = {s.ticker: s for s in SPECS}

    def _world(self, ticker: str) -> StockWorld:
        if ticker not in self._worlds:
            spec = self._spec_by_ticker.get(ticker)
            if spec is None:
                raise CapabilityUnavailable(f"synthetic: unknown ticker {ticker}")
            self._worlds[ticker] = StockWorld(spec)
        return self._worlds[ticker]

    # -- reference -------------------------------------------------------

    def fetch_universe(self, markets: list[str]) -> p.ProviderFetchResult:
        records: list[p.InstrumentPayload] = []
        sectors: set[tuple[str, str]] = set()
        for s in SPECS:
            if s.market not in markets:
                continue
            delisted_at = None
            reason = ""
            if s.acquired:
                delisted_at = date.fromisoformat(s.acquired[1])
                reason = "acquired"
            records.append(
                p.InstrumentPayload(
                    ticker=s.ticker, exchange=s.exchange, market=s.market, name=s.name,
                    sector=s.sector, industry=s.industry, currency=s.currency,
                    security_type="common", listed_at=None,
                    delisted_at=delisted_at, delisting_reason=reason,
                )
            )
            sectors.add((s.market, s.sector))
        for market in markets:
            ccy = "USD" if market == "US" else "GBP"
            records.append(
                p.InstrumentPayload(
                    ticker=f"{market}MKT", exchange="INDEX", market=market,
                    name=f"{market} Market Composite (synthetic)", sector="",
                    industry="", currency=ccy, security_type="index",
                )
            )
            for m, sector in sorted(sectors):
                if m != market:
                    continue
                code = "".join(w[0] for w in sector.split())
                records.append(
                    p.InstrumentPayload(
                        ticker=f"IX{market}{code}", exchange="INDEX", market=market,
                        name=f"{market} {sector} Index (synthetic)", sector=sector,
                        industry="", currency=ccy, security_type="index",
                    )
                )
        return p.ProviderFetchResult(records=records, endpoint="synthetic://universe")

    # -- prices ----------------------------------------------------------

    def fetch_bars(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        end = min(end, WORLD_NOW)
        spec = self._spec_by_ticker.get(ticker)
        if spec is None:
            frame = self._index_frame(ticker)
            if frame is None:
                raise CapabilityUnavailable(f"synthetic: unknown ticker {ticker}")
            bars, currency = frame
        else:
            world = self._world(ticker)
            bars, currency = world.bars, spec.currency
        records = [
            p.BarPayload(
                ticker=ticker, bar_date=ts.date(), open=float(row["open"]),
                high=float(row["high"]), low=float(row["low"]), close=float(row["close"]),
                volume=float(row["volume"]), currency=currency,
            )
            for ts, row in bars.iterrows()
            if start <= ts.date() <= end
        ]
        return p.ProviderFetchResult(records=records, endpoint=f"synthetic://bars/{ticker}")

    def _index_frame(self, ticker: str):
        import pandas as pd

        for market in ("US", "UK"):
            ccy = "USD" if market == "US" else "GBP"
            if ticker == f"{market}MKT":
                closes = market_index_closes(market)
            elif ticker.startswith(f"IX{market}"):
                code = ticker[len(f"IX{market}") :]
                sector = next(
                    (
                        s.sector
                        for s in SPECS
                        if "".join(w[0] for w in s.sector.split()) == code and s.market == market
                    ),
                    None,
                )
                if sector is None:
                    return None
                closes = sector_index_closes(market, sector)
            else:
                continue
            opens = closes.shift(1).fillna(closes)
            df = pd.DataFrame(
                {
                    "open": opens,
                    "high": pd.concat([opens, closes], axis=1).max(axis=1) * 1.004,
                    "low": pd.concat([opens, closes], axis=1).min(axis=1) * 0.996,
                    "close": closes,
                    "volume": 1e9,
                }
            )
            return df, ccy
        return None

    def fetch_actions(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        end = min(end, WORLD_NOW)
        if ticker not in self._spec_by_ticker:
            return p.ProviderFetchResult(records=[], endpoint=f"synthetic://actions/{ticker}")
        world = self._world(ticker)
        records = [a for a in world.actions if start <= a.ex_date <= end]
        return p.ProviderFetchResult(records=records, endpoint=f"synthetic://actions/{ticker}")

    # -- fundamentals ------------------------------------------------------

    def fetch_fundamentals(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        end = min(end, WORLD_NOW)
        if ticker not in self._spec_by_ticker:
            return p.ProviderFetchResult(records=[], endpoint=f"synthetic://fundamentals/{ticker}")
        world = self._world(ticker)
        records = [
            f for f in world.fundamentals if start <= f.published_at.date() <= end
        ]
        return p.ProviderFetchResult(records=records, endpoint=f"synthetic://fundamentals/{ticker}")

    # -- estimates ---------------------------------------------------------

    def fetch_estimates(self, ticker: str, as_of: date) -> p.ProviderFetchResult:
        as_of = min(as_of, WORLD_NOW)
        if ticker not in self._spec_by_ticker:
            return p.ProviderFetchResult(records=[], endpoint=f"synthetic://estimates/{ticker}")
        world = self._world(ticker)
        records = [e for e in world.estimates if e.as_of <= as_of]
        return p.ProviderFetchResult(records=records, endpoint=f"synthetic://estimates/{ticker}")

    def fetch_targets(self, ticker: str, as_of: date) -> p.ProviderFetchResult:
        as_of = min(as_of, WORLD_NOW)
        if ticker not in self._spec_by_ticker:
            return p.ProviderFetchResult(records=[], endpoint=f"synthetic://targets/{ticker}")
        world = self._world(ticker)
        records = [t for t in world.targets if t.as_of <= as_of]
        return p.ProviderFetchResult(records=records, endpoint=f"synthetic://targets/{ticker}")

    # -- news / catalysts ---------------------------------------------------

    def fetch_news(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        end = min(end, WORLD_NOW)
        if ticker not in self._spec_by_ticker:
            return p.ProviderFetchResult(records=[], endpoint=f"synthetic://news/{ticker}")
        world = self._world(ticker)
        records = [n for n in world.news if start <= n.published_at.date() <= end]
        return p.ProviderFetchResult(records=records, endpoint=f"synthetic://news/{ticker}")

    def fetch_catalysts(self, ticker: str, as_of: date) -> p.ProviderFetchResult:
        as_of = min(as_of, WORLD_NOW)
        if ticker not in self._spec_by_ticker:
            return p.ProviderFetchResult(records=[], endpoint=f"synthetic://catalysts/{ticker}")
        world = self._world(ticker)
        records = [
            c
            for c in world.catalysts
            if c.published_at is None or c.published_at.date() <= as_of
        ]
        return p.ProviderFetchResult(records=records, endpoint=f"synthetic://catalysts/{ticker}")

    # -- ownership ----------------------------------------------------------

    def fetch_short_interest(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        end = min(end, WORLD_NOW)
        if ticker not in self._spec_by_ticker:
            return p.ProviderFetchResult(records=[], endpoint=f"synthetic://short/{ticker}")
        world = self._world(ticker)
        records = [s for s in world.short_interest if start <= s.published_at.date() <= end]
        return p.ProviderFetchResult(records=records, endpoint=f"synthetic://short/{ticker}")

    def fetch_insiders(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        end = min(end, WORLD_NOW)
        if ticker not in self._spec_by_ticker:
            return p.ProviderFetchResult(records=[], endpoint=f"synthetic://insiders/{ticker}")
        world = self._world(ticker)
        records = [i for i in world.insiders if start <= i.filed_at.date() <= end]
        return p.ProviderFetchResult(records=records, endpoint=f"synthetic://insiders/{ticker}")

    # -- macro / fx ----------------------------------------------------------

    def fetch_macro(self, series_ids: list[str], start: date, end: date) -> p.ProviderFetchResult:
        end = min(end, WORLD_NOW)
        records = [
            m
            for m in build_macro()
            if m.series_id in series_ids and start <= m.published_at.date() <= end
        ]
        return p.ProviderFetchResult(records=records, endpoint="synthetic://macro")

    def fetch_fx(
        self, pairs: list[tuple[str, str]], start: date, end: date
    ) -> p.ProviderFetchResult:
        end = min(end, WORLD_NOW)
        wanted = set(pairs)
        records = [
            f
            for f in build_fx()
            if (f.base_ccy, f.quote_ccy) in wanted and start <= f.rate_date <= end
        ]
        return p.ProviderFetchResult(records=records, endpoint="synthetic://fx")

    # -- options: honestly unavailable ---------------------------------------

    def fetch_options_summary(self, ticker: str, as_of: date) -> p.ProviderFetchResult:
        raise CapabilityUnavailable(
            "The synthetic provider does not supply options-derived data. "
            "Configure a real options provider to enable IV/skew/unusual-activity signals."
        )

    def health_check(self) -> tuple[bool, str]:
        return True, f"synthetic deterministic dataset (seed={SEED}, now={WORLD_NOW})"
