"""Tiingo adapter (free API key) — US end-of-day prices WITH corporate
actions, and daily FX.

Why it exists: stooq needs no key but blocks some networks and enforces a
daily download cap. Tiingo's free tier (sign up at tiingo.com, copy the
API token) serves reliable US EOD data including per-day ``splitFactor``
and ``divCash`` fields, which lets Vigil reconstruct raw prices and proper
corporate actions — better point-in-time hygiene than stooq's pre-adjusted
series.

Enable with::

    VIGIL_TIINGO_API_KEY=your-token
    VIGIL_PROVIDER_PRICE=tiingo
    VIGIL_PROVIDER_MACRO=tiingo     # supplies FX; macro series unavailable

Limits and honesty:
* Free tier covers US-listed tickers only; London ``.L`` names are refused
  with a clear message (pair with EODHD for UK coverage). Free-tier caps
  (~50-500 unique symbols/month, ~1000 requests/day) comfortably fit a
  personal universe.
* Tiingo's ``close`` field is the raw as-traded price and ``splitFactor``/
  ``divCash`` land on their ex-dates — exactly what the PIT store wants.
* ``fetch_macro`` supplies nothing (no VIX/rates on the free tier) and says
  so; the regime engine degrades gracefully. FX comes from tiingo/fx.
* Index tickers (^SPX) are not served — use the SPY ETF as the US
  benchmark row in universe.yml when using Tiingo.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from vigil.config import get_settings
from vigil.providers import base as p
from vigil.providers.base import CapabilityUnavailable, ProviderError
from vigil.providers.registry import HttpFetcher

BASE = "https://api.tiingo.com"


class TiingoProvider:
    name = "tiingo"

    def __init__(self) -> None:
        self._key = get_settings().tiingo_api_key.strip().strip('"').strip("'")
        if not self._key:
            raise CapabilityUnavailable(
                "Tiingo is not configured. Create a free account at tiingo.com and "
                "set VIGIL_TIINGO_API_KEY to your API token."
            )
        # Tiingo wants an explicit JSON content type on every request and
        # accepts header auth as well as the token query param — send both.
        self._http = HttpFetcher(
            base_headers={
                "Content-Type": "application/json",
                "Authorization": f"Token {self._key}",
            },
            min_interval_s=0.15,
        )
        # fetch_bars and fetch_actions read the SAME response; memoise the
        # last request so ingest doesn't pay for (or get rate-limited by)
        # a duplicate call per ticker.
        self._memo_key: tuple[str, date, date] | None = None
        self._memo_val: tuple[list, str, datetime] | None = None

    def _symbol(self, ticker: str) -> str:
        t = ticker.upper()
        if t.endswith(".L"):
            raise CapabilityUnavailable(
                f"Tiingo's free tier is US-only; it cannot serve {ticker}. UK names "
                "need EODHD (see docs/REAL_DATA.md) or can be removed from universe.yml."
            )
        if t.startswith("^"):
            raise CapabilityUnavailable(
                f"Tiingo does not serve index symbols like {ticker}. Use the SPY ETF "
                "row as the US benchmark in universe.yml (see env.tiingo.example)."
            )
        return t.replace(".", "-").lower()  # BRK.B -> brk-b

    def _daily(self, ticker: str, start: date, end: date) -> tuple[list, str, datetime]:
        key = (ticker.upper(), start, end)
        if self._memo_key == key and self._memo_val is not None:
            return self._memo_val
        symbol = self._symbol(ticker)
        body, retrieved_at, _ = self._http.get(
            f"{BASE}/tiingo/daily/{symbol}/prices",
            params={
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "token": self._key,
            },
        )
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"tiingo daily/{symbol}: response was not JSON: {exc}") from exc
        if isinstance(data, dict):  # error payloads arrive as {"detail": ...}
            raise CapabilityUnavailable(
                f"tiingo daily/{symbol}: {data.get('detail') or data}"
            )
        if not isinstance(data, list):
            raise ProviderError(f"tiingo daily/{symbol}: unexpected payload shape")
        self._memo_key, self._memo_val = key, (data, body, retrieved_at)
        return self._memo_val

    @staticmethod
    def _row_date(row: dict) -> date:
        return date.fromisoformat(str(row["date"])[:10])

    def fetch_bars(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        data, raw, retrieved_at = self._daily(ticker, start, end)
        records: list[p.BarPayload] = []
        warnings: list[str] = []
        for row in data:
            try:
                o, h, lo, c = (row.get(k) for k in ("open", "high", "low", "close"))
                if None in (o, h, lo, c):
                    continue
                records.append(
                    p.BarPayload(
                        ticker=ticker,
                        bar_date=self._row_date(row),
                        open=float(o), high=float(h), low=float(lo), close=float(c),
                        volume=float(row.get("volume") or 0.0),
                        currency="USD",
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                warnings.append(f"bar row skipped: {exc}")
        return p.ProviderFetchResult(
            records=records, raw=raw[:500_000],
            endpoint=f"{BASE}/tiingo/daily/{ticker.lower()}/prices",
            retrieved_at=retrieved_at, warnings=warnings,
        )

    def fetch_actions(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        data, _raw, retrieved_at = self._daily(ticker, start, end)
        records: list[p.ActionPayload] = []
        warnings: list[str] = []
        for row in data:
            try:
                bar_date = self._row_date(row)
                split = float(row.get("splitFactor") or 1.0)
                if split > 0 and split != 1.0:
                    records.append(
                        p.ActionPayload(
                            ticker=ticker, kind="split", ex_date=bar_date,
                            factor=split, detail=f"split factor {split}",
                        )
                    )
                div = float(row.get("divCash") or 0.0)
                if div > 0:
                    records.append(
                        p.ActionPayload(
                            ticker=ticker, kind="dividend", ex_date=bar_date,
                            amount=div, detail=f"dividend {div}",
                        )
                    )
            except (KeyError, ValueError, TypeError) as exc:
                warnings.append(f"action row skipped: {exc}")
        return p.ProviderFetchResult(
            records=records, endpoint=f"{BASE}/tiingo/daily/{ticker.lower()}/prices",
            retrieved_at=retrieved_at, warnings=warnings,
        )

    # -- FX (capability 'macro' also carries fetch_fx) ---------------------

    def fetch_fx(
        self, pairs: list[tuple[str, str]], start: date, end: date
    ) -> p.ProviderFetchResult:
        records: list[p.FxPayload] = []
        warnings: list[str] = []
        retrieved_at = None
        for base_ccy, quote_ccy in pairs:
            pair = f"{base_ccy}{quote_ccy}".lower()
            try:
                body, retrieved_at, _ = self._http.get(
                    f"{BASE}/tiingo/fx/{pair}/prices",
                    params={
                        "startDate": start.isoformat(),
                        "endDate": end.isoformat(),
                        "resampleFreq": "1day",
                        "token": self._key,
                    },
                )
                data: Any = json.loads(body)
            except (ProviderError, CapabilityUnavailable, json.JSONDecodeError) as exc:
                warnings.append(f"fx {base_ccy}/{quote_ccy}: {exc}")
                continue
            if not isinstance(data, list):
                warnings.append(f"fx {base_ccy}/{quote_ccy}: {data}")
                continue
            for row in data:
                try:
                    rate = float(row.get("close"))
                    records.append(
                        p.FxPayload(
                            base_ccy=base_ccy, quote_ccy=quote_ccy,
                            rate_date=self._row_date(row), rate=rate,
                        )
                    )
                except (KeyError, ValueError, TypeError):
                    continue
        return p.ProviderFetchResult(
            records=records, endpoint=f"{BASE}/tiingo/fx", retrieved_at=retrieved_at,
            warnings=warnings,
        )

    def fetch_macro(
        self, series_ids: list[str], start: date, end: date
    ) -> p.ProviderFetchResult:
        return p.ProviderFetchResult(
            records=[],
            endpoint=f"{BASE} (macro)",
            warnings=[
                f"tiingo free tier does not supply macro series '{sid}'"
                for sid in series_ids
            ],
        )

    def health_check(self) -> tuple[bool, str]:
        try:
            body, _, latency = self._http.get(
                f"{BASE}/api/test", params={"token": self._key}
            )
            ok = "you successfully sent a request" in body.lower() or "message" in body.lower()
            return ok, f"latency {latency:.0f}ms" if ok else f"unexpected: {body[:80]}"
        except Exception as exc:
            return False, str(exc)
