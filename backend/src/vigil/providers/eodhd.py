"""EODHD adapter (paid, keyed) — prices with corporate actions, global
fundamentals (UK included), analyst estimates/targets, news, earnings
calendar, FX and a macro subset.

Enable by setting ``VIGIL_EODHD_API_KEY`` and pointing capabilities at it::

    VIGIL_EODHD_API_KEY=your-token
    VIGIL_PROVIDER_PRICE=eodhd
    VIGIL_PROVIDER_FUNDAMENTALS=eodhd
    VIGIL_PROVIDER_ESTIMATES=eodhd
    VIGIL_PROVIDER_NEWS=eodhd
    VIGIL_PROVIDER_MACRO=eodhd

Symbol mapping: US ``AAPL`` -> ``AAPL.US``; London ``VOD.L`` -> ``VOD.LSE``;
indices ``^SPX`` -> ``GSPC.INDX`` (see ``_INDEX_MAP``). LSE prices and
dividends are quoted in pence (GBX) and converted to GBP at ingest.

Honesty notes (see docs/LIMITATIONS.md):
* Built against EODHD's documented API but NOT verified against the live
  service in this environment (no key/network). Contract tests run on
  canned fixture responses; expect small field-name fixes on first real
  use — every parser fails soft with a recorded warning, never silently.
* Estimates/targets are CURRENT snapshots. EODHD's earnings trend carries
  7/30/60/90-day-ago consensus fields, which populate the revision inputs,
  but a true point-in-time estimate history only accumulates from your own
  repeated daily ingests. Backtests over earlier periods must treat
  estimate-driven signals accordingly.
* Universe listing is intentionally NOT taken from EODHD's exchange lists —
  keep the deliberate, editable ``universe.yml`` (static provider).
* Short interest / insider transactions are not mapped in v1.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from typing import Any

from vigil.config import get_settings
from vigil.providers import base as p
from vigil.providers.base import CapabilityUnavailable, ProviderError
from vigil.providers.registry import HttpFetcher

BASE = "https://eodhd.com/api"

_INDEX_MAP = {"^SPX": "GSPC.INDX", "^UKX": "FTSE.INDX", "^FTM": "FTSE.INDX", "^VIX": "VIX.INDX"}


def _num(value: Any) -> float | None:
    """EODHD serialises many numerics as strings, with 'NA'/None for gaps."""
    if value in (None, "", "NA", "None"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class EodhdProvider:
    name = "eodhd"

    def __init__(self) -> None:
        self._key = get_settings().eodhd_api_key
        if not self._key:
            raise CapabilityUnavailable(
                "EODHD is not configured. Set VIGIL_EODHD_API_KEY (from eodhd.com) "
                "to enable this adapter."
            )
        self._http = HttpFetcher(min_interval_s=0.1)

    # ------------------------------------------------------------------
    # plumbing
    # ------------------------------------------------------------------

    def _symbol(self, ticker: str) -> tuple[str, bool]:
        """(eodhd symbol, is_gbx) — LSE quotes arrive in pence."""
        t = ticker.upper()
        if t in _INDEX_MAP:
            return _INDEX_MAP[t], False
        if t.endswith(".L"):
            return t.removesuffix(".L") + ".LSE", True
        if "." in t:
            return t, False
        return f"{t}.US", False

    def _get_json(self, path: str, params: dict) -> tuple[Any, str, datetime]:
        params = {**params, "api_token": self._key, "fmt": "json"}
        body, retrieved_at, _ = self._http.get(f"{BASE}/{path}", params=params)
        try:
            return json.loads(body), body, retrieved_at
        except json.JSONDecodeError as exc:
            raise ProviderError(f"EODHD {path}: response was not JSON: {exc}") from exc

    # ------------------------------------------------------------------
    # reference — deliberately not served (see module docstring)
    # ------------------------------------------------------------------

    def fetch_universe(self, markets: list[str]) -> p.ProviderFetchResult:
        raise CapabilityUnavailable(
            "Keep VIGIL_PROVIDER_REFERENCE=static with your universe.yml — the "
            "universe stays a deliberate, editable list even with EODHD data."
        )

    # ------------------------------------------------------------------
    # prices + corporate actions
    # ------------------------------------------------------------------

    def fetch_bars(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        symbol, is_gbx = self._symbol(ticker)
        data, raw, retrieved_at = self._get_json(
            f"eod/{symbol}", {"from": start.isoformat(), "to": end.isoformat(), "period": "d"}
        )
        if not isinstance(data, list):
            raise ProviderError(f"EODHD eod/{symbol}: unexpected payload shape")
        scale = 0.01 if is_gbx else 1.0
        currency = "GBP" if is_gbx else ("GBP" if ticker.upper().endswith(".L") else "USD")
        records: list[p.BarPayload] = []
        warnings: list[str] = []
        for row in data:
            try:
                o = _num(row.get("open"))
                h = _num(row.get("high"))
                lo = _num(row.get("low"))
                c = _num(row.get("close"))
                if o is None or h is None or lo is None or c is None:
                    continue
                records.append(
                    p.BarPayload(
                        ticker=ticker,
                        bar_date=date.fromisoformat(str(row["date"])),
                        open=o * scale, high=h * scale, low=lo * scale, close=c * scale,
                        volume=_num(row.get("volume")) or 0.0,
                        currency=currency,
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                warnings.append(f"bar row skipped: {exc}")
        return p.ProviderFetchResult(
            records=records, raw=raw[:500_000], endpoint=f"{BASE}/eod/{symbol}",
            retrieved_at=retrieved_at, warnings=warnings,
        )

    def fetch_actions(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        symbol, is_gbx = self._symbol(ticker)
        records: list[p.ActionPayload] = []
        warnings: list[str] = []
        raw_parts: list[str] = []
        retrieved_at = None
        try:
            splits, raw, retrieved_at = self._get_json(
                f"splits/{symbol}", {"from": start.isoformat(), "to": end.isoformat()}
            )
            raw_parts.append(raw)
            for row in splits if isinstance(splits, list) else []:
                try:
                    # "4.000000/1.000000" -> factor 4.0
                    num, _, den = str(row["split"]).partition("/")
                    factor = float(num) / float(den or 1)
                    if factor > 0 and factor != 1.0:
                        records.append(
                            p.ActionPayload(
                                ticker=ticker, kind="split",
                                ex_date=date.fromisoformat(str(row["date"])),
                                factor=factor, detail=f"split {row['split']}",
                            )
                        )
                except (KeyError, ValueError, ZeroDivisionError) as exc:
                    warnings.append(f"split row skipped: {exc}")
        except (ProviderError, CapabilityUnavailable) as exc:
            warnings.append(f"splits fetch failed: {exc}")
        try:
            divs, raw, retrieved_at = self._get_json(
                f"div/{symbol}", {"from": start.isoformat(), "to": end.isoformat()}
            )
            raw_parts.append(raw)
            scale = 0.01 if is_gbx else 1.0
            for row in divs if isinstance(divs, list) else []:
                try:
                    amount = _num(row.get("value"))
                    if amount and amount > 0:
                        records.append(
                            p.ActionPayload(
                                ticker=ticker, kind="dividend",
                                ex_date=date.fromisoformat(str(row["date"])),
                                amount=amount * scale,
                                detail=f"dividend {amount * scale:.4f}",
                            )
                        )
                except (KeyError, ValueError) as exc:
                    warnings.append(f"dividend row skipped: {exc}")
        except (ProviderError, CapabilityUnavailable) as exc:
            warnings.append(f"dividends fetch failed: {exc}")
        return p.ProviderFetchResult(
            records=records, raw="\n".join(raw_parts)[:500_000],
            endpoint=f"{BASE}/splits+div/{symbol}", retrieved_at=retrieved_at,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # fundamentals (joins the three quarterly statements by period end)
    # ------------------------------------------------------------------

    _INCOME = {
        "revenue": "totalRevenue", "gross_profit": "grossProfit",
        "operating_income": "operatingIncome", "net_income": "netIncome",
        "interest_expense": "interestExpense",
    }
    _BALANCE = {
        "total_assets": "totalAssets", "total_equity": "totalStockholderEquity",
        "total_debt": "shortLongTermDebtTotal", "cash_and_equivalents": "cashAndEquivalents",
        "current_assets": "totalCurrentAssets", "current_liabilities": "totalCurrentLiabilities",
        "receivables": "netReceivables", "inventory": "inventory",
        "goodwill_intangibles": "goodWill", "debt_due_within_1y": "shortTermDebt",
        "shares_diluted": "commonStockSharesOutstanding",
    }
    _CASHFLOW = {
        "operating_cash_flow": "totalCashFromOperatingActivities",
        "capex": "capitalExpenditures",
        "dividends_paid": "dividendsPaid",
        "buybacks": "salePurchaseOfStock",
        "stock_based_comp": "stockBasedCompensation",
    }

    def fetch_fundamentals(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        symbol, is_gbx = self._symbol(ticker)
        data, raw, retrieved_at = self._get_json(f"fundamentals/{symbol}", {})
        if not isinstance(data, dict):
            raise ProviderError(f"EODHD fundamentals/{symbol}: unexpected payload shape")
        fin = data.get("Financials", {}) or {}
        currency = (data.get("General", {}) or {}).get("CurrencyCode") or (
            "GBP" if ticker.upper().endswith(".L") else "USD"
        )
        # LSE statements are reported in actual currency units (GBP/USD),
        # not pence — no GBX scaling here, only prices/dividends carry GBX.
        warnings: list[str] = []
        by_period: dict[str, dict] = {}

        def collect(section: str, mapping: dict[str, str]) -> None:
            quarterly = ((fin.get(section) or {}).get("quarterly") or {})
            for iso, row in quarterly.items():
                if not isinstance(row, dict):
                    continue
                slot = by_period.setdefault(iso, {"fields": {}, "filing_date": None})
                for ours, theirs in mapping.items():
                    val = _num(row.get(theirs))
                    if val is not None and ours not in slot["fields"]:
                        slot["fields"][ours] = abs(val) if ours == "capex" else val
                fd = row.get("filing_date")
                if fd and not slot["filing_date"]:
                    slot["filing_date"] = str(fd)

        collect("Income_Statement", self._INCOME)
        collect("Balance_Sheet", self._BALANCE)
        collect("Cash_Flow", self._CASHFLOW)

        records: list[p.FundamentalPayload] = []
        for iso in sorted(by_period):
            slot = by_period[iso]
            fields = slot["fields"]
            if "revenue" not in fields and "net_income" not in fields:
                continue
            try:
                period_end = date.fromisoformat(iso)
            except ValueError:
                warnings.append(f"period key skipped: {iso}")
                continue
            if slot["filing_date"]:
                published = datetime.combine(date.fromisoformat(slot["filing_date"]), time(16, 0))
            else:
                published = datetime.combine(period_end + timedelta(days=60), time(16, 0))
                warnings.append(
                    f"{iso}: no filing_date from EODHD — assumed a conservative 60-day "
                    "publication lag (documented point-in-time approximation)"
                )
            if not (start <= published.date() <= end):
                continue
            ni, sh = fields.get("net_income"), fields.get("shares_diluted")
            if ni is not None and sh and "eps_diluted" not in fields:
                fields["eps_diluted"] = ni / sh
            records.append(
                p.FundamentalPayload(
                    ticker=ticker, period_end=period_end, period_type="Q",
                    published_at=published, currency=str(currency), fields=fields,
                    source_reference=f"{BASE}/fundamentals/{symbol}",
                    shares_outstanding=fields.get("shares_diluted"),
                )
            )
        _ = is_gbx
        return p.ProviderFetchResult(
            records=records, raw=raw[:2_000_000], endpoint=f"{BASE}/fundamentals/{symbol}",
            retrieved_at=retrieved_at, warnings=warnings,
        )

    # ------------------------------------------------------------------
    # estimates & targets (snapshot semantics — see module docstring)
    # ------------------------------------------------------------------

    def fetch_estimates(self, ticker: str, as_of: date) -> p.ProviderFetchResult:
        symbol, _ = self._symbol(ticker)
        data, raw, retrieved_at = self._get_json(f"fundamentals/{symbol}", {"filter": "Earnings"})
        trend = (data or {}).get("Trend") or {}
        records: list[p.EstimatePayload] = []
        warnings: list[str] = []
        for iso, row in (trend.items() if isinstance(trend, dict) else []):
            if not isinstance(row, dict):
                continue
            try:
                period_end = date.fromisoformat(str(row.get("date") or iso))
            except ValueError:
                continue
            if period_end < as_of - timedelta(days=30):
                continue
            mean = _num(row.get("earningsEstimateAvg"))
            count = int(_num(row.get("earningsEstimateNumberOfAnalysts")) or 0)
            if mean is None:
                continue
            label = f"FY{period_end.year}" if str(row.get("period", "")).startswith("0y") or (
                period_end.month == 12
            ) else f"P{period_end.isoformat()}"
            records.append(
                p.EstimatePayload(
                    ticker=ticker, as_of=as_of, metric="eps", fiscal_label=label,
                    period_end=period_end, mean=mean,
                    high=_num(row.get("earningsEstimateHigh")),
                    low=_num(row.get("earningsEstimateLow")),
                    analyst_count=count,
                    mean_30d_ago=_num(row.get("epsTrend30daysAgo")),
                    mean_90d_ago=_num(row.get("epsTrend90daysAgo")),
                    up_revisions_30d=int(_num(row.get("epsRevisionsUpLast30days")) or 0),
                    down_revisions_30d=int(_num(row.get("epsRevisionsDownLast30days")) or 0),
                )
            )
            rev_mean = _num(row.get("revenueEstimateAvg"))
            if rev_mean is not None:
                records.append(
                    p.EstimatePayload(
                        ticker=ticker, as_of=as_of, metric="revenue", fiscal_label=label,
                        period_end=period_end, mean=rev_mean,
                        analyst_count=int(
                            _num(row.get("revenueEstimateNumberOfAnalysts")) or count
                        ),
                    )
                )
        if not records:
            warnings.append("no usable earnings-trend rows (thin coverage or field change)")
        return p.ProviderFetchResult(
            records=records, raw=raw[:500_000],
            endpoint=f"{BASE}/fundamentals/{symbol}?filter=Earnings",
            retrieved_at=retrieved_at, warnings=warnings,
        )

    def fetch_targets(self, ticker: str, as_of: date) -> p.ProviderFetchResult:
        symbol, _ = self._symbol(ticker)
        data, raw, retrieved_at = self._get_json(
            f"fundamentals/{symbol}", {"filter": "AnalystRatings"}
        )
        ratings = data if isinstance(data, dict) else {}
        mean = _num(ratings.get("TargetPrice"))
        counts = [
            int(_num(ratings.get(k)) or 0)
            for k in ("StrongBuy", "Buy", "Hold", "Sell", "StrongSell")
        ]
        records: list[p.TargetPayload] = []
        if mean is not None:
            records.append(
                p.TargetPayload(
                    ticker=ticker, as_of=as_of,
                    currency="GBP" if ticker.upper().endswith(".L") else "USD",
                    mean=mean, analyst_count=sum(counts),
                )
            )
        return p.ProviderFetchResult(
            records=records, raw=raw[:200_000],
            endpoint=f"{BASE}/fundamentals/{symbol}?filter=AnalystRatings",
            retrieved_at=retrieved_at,
            warnings=[] if records else ["no analyst target published"],
        )

    # ------------------------------------------------------------------
    # news & catalysts
    # ------------------------------------------------------------------

    def fetch_news(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        symbol, _ = self._symbol(ticker)
        data, raw, retrieved_at = self._get_json(
            "news", {"s": symbol, "from": start.isoformat(), "to": end.isoformat(),
                     "limit": 200},
        )
        records: list[p.NewsPayload] = []
        warnings: list[str] = []
        for i, row in enumerate(data if isinstance(data, list) else []):
            try:
                published = datetime.fromisoformat(str(row["date"]).replace("Z", "+00:00"))
                sentiment = 0.0
                sent = row.get("sentiment")
                if isinstance(sent, dict):
                    sentiment = _num(sent.get("polarity")) or 0.0
                records.append(
                    p.NewsPayload(
                        ticker=ticker,
                        external_id=str(row.get("link") or f"eodhd-{symbol}-{i}"),
                        published_at=published,
                        headline=str(row.get("title", ""))[:290],
                        summary=str(row.get("content", ""))[:1000],
                        source_name="EODHD news",
                        # EODHD does not classify source types; commentary is
                        # the conservative default weighting.
                        source_type="market_commentary",
                        url=str(row.get("link", "")),
                        sentiment=max(-1.0, min(1.0, sentiment)),
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                warnings.append(f"news row skipped: {exc}")
        return p.ProviderFetchResult(
            records=records, raw=raw[:500_000], endpoint=f"{BASE}/news?s={symbol}",
            retrieved_at=retrieved_at, warnings=warnings,
        )

    def fetch_catalysts(self, ticker: str, as_of: date) -> p.ProviderFetchResult:
        symbol, _ = self._symbol(ticker)
        data, raw, retrieved_at = self._get_json(
            "calendar/earnings",
            {"symbols": symbol, "from": (as_of - timedelta(days=180)).isoformat(),
             "to": (as_of + timedelta(days=120)).isoformat()},
        )
        rows = (data or {}).get("earnings", data) if isinstance(data, dict) else data
        records: list[p.CatalystPayload] = []
        warnings: list[str] = []
        for row in rows if isinstance(rows, list) else []:
            try:
                report = date.fromisoformat(str(row["report_date"]))
                actual = _num(row.get("actual"))
                estimate = _num(row.get("estimate"))
                resolved = report <= as_of and actual is not None
                outcome = None
                if resolved and actual is not None and estimate:
                    outcome = f"EPS surprise {(actual - estimate) / abs(estimate) * 100:+.1f}%"
                elif resolved:
                    outcome = f"EPS reported {actual}"
                records.append(
                    p.CatalystPayload(
                        ticker=ticker, external_id=f"eodhd-earn-{report.isoformat()}",
                        kind="earnings", expected_date=report,
                        date_confirmed=True,
                        description="Quarterly results",
                        binary=False,
                        resolved=resolved, outcome=outcome,
                        outcome_date=report if resolved else None,
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                warnings.append(f"calendar row skipped: {exc}")
        return p.ProviderFetchResult(
            records=records, raw=raw[:200_000],
            endpoint=f"{BASE}/calendar/earnings?symbols={symbol}",
            retrieved_at=retrieved_at, warnings=warnings,
        )

    # ------------------------------------------------------------------
    # ownership — not mapped in v1 (honest unavailability)
    # ------------------------------------------------------------------

    def fetch_short_interest(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        raise CapabilityUnavailable("EODHD short-interest mapping not implemented in v1")

    def fetch_insiders(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        raise CapabilityUnavailable("EODHD insider-transaction mapping not implemented in v1")

    # ------------------------------------------------------------------
    # macro & FX
    # ------------------------------------------------------------------

    def fetch_fx(
        self, pairs: list[tuple[str, str]], start: date, end: date
    ) -> p.ProviderFetchResult:
        records: list[p.FxPayload] = []
        warnings: list[str] = []
        retrieved_at = None
        for base_ccy, quote_ccy in pairs:
            symbol = f"{base_ccy}{quote_ccy}.FOREX"
            try:
                data, _raw, retrieved_at = self._get_json(
                    f"eod/{symbol}", {"from": start.isoformat(), "to": end.isoformat()}
                )
            except (ProviderError, CapabilityUnavailable) as exc:
                warnings.append(f"fx {base_ccy}/{quote_ccy}: {exc}")
                continue
            for row in data if isinstance(data, list) else []:
                rate = _num(row.get("close"))
                if rate is None:
                    continue
                try:
                    records.append(
                        p.FxPayload(
                            base_ccy=base_ccy, quote_ccy=quote_ccy,
                            rate_date=date.fromisoformat(str(row["date"])), rate=rate,
                        )
                    )
                except (KeyError, ValueError):
                    continue
        return p.ProviderFetchResult(
            records=records, endpoint=f"{BASE}/eod/<pair>.FOREX",
            retrieved_at=retrieved_at, warnings=warnings,
        )

    def fetch_macro(
        self, series_ids: list[str], start: date, end: date
    ) -> p.ProviderFetchResult:
        """vix via VIX.INDX; us/uk CPI via the macro-indicator API. Policy
        rates and credit spreads are reported unavailable (no clean EODHD
        series) — the regime engine degrades gracefully."""
        records: list[p.MacroPayload] = []
        warnings: list[str] = []
        retrieved_at = None
        if "vix" in series_ids:
            try:
                data, _raw, retrieved_at = self._get_json(
                    "eod/VIX.INDX", {"from": start.isoformat(), "to": end.isoformat()}
                )
                for row in data if isinstance(data, list) else []:
                    value = _num(row.get("close"))
                    if value is None:
                        continue
                    obs = date.fromisoformat(str(row["date"]))
                    records.append(
                        p.MacroPayload(
                            series_id="vix", obs_date=obs, value=value,
                            published_at=datetime.combine(obs, time(21, 0)),
                        )
                    )
            except (ProviderError, CapabilityUnavailable) as exc:
                warnings.append(f"vix: {exc}")
        for sid, country in (("us_cpi_yoy", "USA"), ("uk_cpi_yoy", "GBR")):
            if sid not in series_ids:
                continue
            try:
                data, _raw, retrieved_at = self._get_json(
                    f"macro-indicator/{country}",
                    {"indicator": "inflation_consumer_prices_annual"},
                )
                for row in data if isinstance(data, list) else []:
                    value = _num(row.get("Value"))
                    if value is None:
                        continue
                    obs = date.fromisoformat(str(row["Date"]))
                    if not (start <= obs <= end):
                        continue
                    records.append(
                        p.MacroPayload(
                            series_id=sid, obs_date=obs, value=value,
                            # Annual CPI publishes well after period end; a
                            # 14-day lag is a conservative PIT approximation.
                            published_at=datetime.combine(obs + timedelta(days=14), time(13, 0)),
                        )
                    )
            except (ProviderError, CapabilityUnavailable) as exc:
                warnings.append(f"{sid}: {exc}")
        unsupported = [
            s for s in series_ids
            if s not in ("vix", "us_cpi_yoy", "uk_cpi_yoy")
        ]
        warnings.extend(f"EODHD adapter does not map macro series '{s}'" for s in unsupported)
        return p.ProviderFetchResult(
            records=records, endpoint=f"{BASE}/macro-indicator", retrieved_at=retrieved_at,
            warnings=warnings,
        )

    def health_check(self) -> tuple[bool, str]:
        try:
            data, _, _ = self._get_json("eod/AAPL.US", {"filter": "last_close"})
            return True, f"reachable (AAPL last close {data})"
        except Exception as exc:
            return False, str(exc)
