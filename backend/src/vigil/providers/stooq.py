"""Stooq end-of-day price adapter (free, keyless).

Real-data adapter for daily OHLCV. Caveats (documented, surfaced as
warnings): Stooq serves *split-adjusted* prices and does NOT publish
corporate actions, so ``fetch_actions`` returns empty with a warning and
the PIT split machinery has nothing to work with — pair this adapter with
an actions-capable vendor for production use. UK tickers are quoted in GBX
(pence) on stooq; this adapter converts to GBP at ingest.

Ticker mapping: US -> ``aapl.us``; UK (LSE) -> ``vod.uk``.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, time

from vigil.config import get_settings
from vigil.providers import base as p
from vigil.providers.base import CapabilityUnavailable
from vigil.providers.registry import HttpFetcher

BASE_URL = "https://stooq.com/q/d/l/"


class StooqProvider:
    name = "stooq"

    def __init__(self) -> None:
        # stooq rejects (404s) requests without a browser-like identity, so
        # present one; still throttled to 1 req/s to stay polite.
        self._http = HttpFetcher(
            base_headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
                ),
                "Accept": "text/csv,text/plain,*/*",
                "Referer": "https://stooq.com/",
            },
            min_interval_s=1.0,
        )

    def _symbol(self, ticker: str, market: str | None = None) -> str:
        t = ticker.lower()
        if t.startswith("^"):
            return t  # index symbols (^spx, ^ukx, ^vix) pass through unchanged
        if market == "UK" or t.endswith(".l"):
            return t.removesuffix(".l") + ".uk"
        return t if "." in t else f"{t}.us"

    def fetch_universe(self, markets: list[str]) -> p.ProviderFetchResult:
        raise CapabilityUnavailable(
            "stooq does not serve a screener/universe endpoint. Configure the "
            "universe explicitly (see docs/PROVIDERS.md) or use another vendor."
        )

    def fetch_bars(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        settings = get_settings()
        market = "UK" if ticker.upper().endswith(".L") else None
        symbol = self._symbol(ticker, market)
        params = {
            "s": symbol,
            "d1": start.strftime("%Y%m%d"),
            "d2": end.strftime("%Y%m%d"),
            "i": "d",
        }
        body, retrieved_at, _latency = self._http.get(BASE_URL, params=params)
        if body.strip().lower().startswith("no data") or "<html" in body[:200].lower():
            raise CapabilityUnavailable(f"stooq: no data for symbol {symbol}")
        is_gbx = symbol.endswith(".uk")
        scale = 0.01 if is_gbx else 1.0  # GBX -> GBP
        currency = "GBP" if is_gbx else "USD"
        records: list[p.BarPayload] = []
        warnings = ["stooq prices are split-adjusted; corporate actions unavailable"]
        reader = csv.DictReader(io.StringIO(body))
        for row in reader:
            try:
                records.append(
                    p.BarPayload(
                        ticker=ticker,
                        bar_date=date.fromisoformat(row["Date"]),
                        open=float(row["Open"]) * scale,
                        high=float(row["High"]) * scale,
                        low=float(row["Low"]) * scale,
                        close=float(row["Close"]) * scale,
                        volume=float(row.get("Volume") or 0.0),
                        currency=currency,
                    )
                )
            except (KeyError, ValueError) as exc:
                warnings.append(f"row skipped: {exc}")
        _ = settings
        return p.ProviderFetchResult(
            records=records,
            raw=body[:500_000],
            endpoint=f"{BASE_URL}?s={symbol}",
            retrieved_at=retrieved_at,
            warnings=warnings,
        )

    def fetch_actions(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        return p.ProviderFetchResult(
            records=[],
            endpoint="stooq://actions",
            warnings=[
                "stooq does not publish corporate actions; splits/dividends must "
                "come from another provider"
            ],
        )

    def fetch_fx(
        self, pairs: list[tuple[str, str]], start: date, end: date
    ) -> p.ProviderFetchResult:
        """Daily FX closes via stooq's forex symbols (e.g. USDGBP -> usdgbp)."""
        records: list[p.FxPayload] = []
        warnings: list[str] = []
        retrieved_at = None
        for base_ccy, quote_ccy in pairs:
            symbol = f"{base_ccy}{quote_ccy}".lower()
            params = {
                "s": symbol,
                "d1": start.strftime("%Y%m%d"),
                "d2": end.strftime("%Y%m%d"),
                "i": "d",
            }
            try:
                body, retrieved_at, _ = self._http.get(BASE_URL, params=params)
            except Exception as exc:
                warnings.append(f"fx {base_ccy}/{quote_ccy}: {exc}")
                continue
            if "Date" not in body[:100]:
                warnings.append(f"fx {base_ccy}/{quote_ccy}: no data for symbol {symbol}")
                continue
            for row in csv.DictReader(io.StringIO(body)):
                try:
                    records.append(
                        p.FxPayload(
                            base_ccy=base_ccy,
                            quote_ccy=quote_ccy,
                            rate_date=date.fromisoformat(row["Date"]),
                            rate=float(row["Close"]),
                        )
                    )
                except (KeyError, ValueError) as exc:
                    warnings.append(f"fx row skipped: {exc}")
        return p.ProviderFetchResult(
            records=records, endpoint=f"{BASE_URL} (forex)", retrieved_at=retrieved_at,
            warnings=warnings,
        )

    def fetch_macro(
        self, series_ids: list[str], start: date, end: date
    ) -> p.ProviderFetchResult:
        """Only 'vix' is available from stooq (^vix daily closes). Rates,
        CPI and credit spreads need another source and are reported as
        unavailable rather than faked."""
        records: list[p.MacroPayload] = []
        warnings = [
            f"stooq cannot supply macro series '{sid}'"
            for sid in series_ids
            if sid != "vix"
        ]
        retrieved_at = None
        if "vix" in series_ids:
            params = {
                "s": "^vix",
                "d1": start.strftime("%Y%m%d"),
                "d2": end.strftime("%Y%m%d"),
                "i": "d",
            }
            try:
                body, retrieved_at, _ = self._http.get(BASE_URL, params=params)
                for row in csv.DictReader(io.StringIO(body)):
                    try:
                        obs = date.fromisoformat(row["Date"])
                        records.append(
                            p.MacroPayload(
                                series_id="vix",
                                obs_date=obs,
                                value=float(row["Close"]),
                                published_at=datetime.combine(obs, time(21, 0)),
                            )
                        )
                    except (KeyError, ValueError):
                        continue
            except Exception as exc:
                warnings.append(f"vix fetch failed: {exc}")
        return p.ProviderFetchResult(
            records=records, endpoint=f"{BASE_URL} (^vix)", retrieved_at=retrieved_at,
            warnings=warnings,
        )

    def health_check(self) -> tuple[bool, str]:
        try:
            body, _, latency = self._http.get(
                BASE_URL, params={"s": "spy.us", "i": "d", "d1": "20240102", "d2": "20240105"}
            )
            ok = "Date" in body[:100]
            return ok, f"latency {latency:.0f}ms" if ok else "unexpected response shape"
        except Exception as exc:
            return False, str(exc)
