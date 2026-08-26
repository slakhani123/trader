"""SEC EDGAR company-facts adapter (free, keyless, US securities only).

Uses the official XBRL ``companyfacts`` API. The SEC requires a descriptive
User-Agent ("name email"); set ``VIGIL_EDGAR_USER_AGENT`` or the adapter
refuses to run (fair-access policy). Rate-limited to <10 req/s per SEC
guidance — this adapter stays at 2 req/s.

Point-in-time discipline: ``published_at`` is the XBRL ``filed`` date of
the report that carried the fact, so a backtest never sees a number before
its filing. Restated values appear as new observations from later filings.

Simplifications (documented): only a core us-gaap concept set is mapped;
dimensional/segment facts are ignored; fiscal quarters are detected from
form type + period duration. Good enough for research screens; a
commercial fundamentals feed remains the recommended production source.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from typing import Any

from vigil.config import get_settings
from vigil.providers import base as p
from vigil.providers.base import CapabilityUnavailable, ProviderError
from vigil.providers.registry import HttpFetcher

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# us-gaap concept -> FundamentalRecord field (first match wins).
CONCEPTS: dict[str, list[str]] = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "SalesRevenueNet"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "interest_expense": ["InterestExpense"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "dividends_paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "stock_based_comp": ["ShareBasedCompensation"],
    "total_assets": ["Assets"],
    "total_equity": ["StockholdersEquity",
                     "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "total_debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "cash_and_equivalents": ["CashAndCashEquivalentsAtCarryingValue"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "receivables": ["AccountsReceivableNetCurrent"],
    "inventory": ["InventoryNet"],
    "goodwill_intangibles": ["Goodwill"],
    "debt_due_within_1y": ["LongTermDebtCurrent"],
}

FLOW_FIELDS = {
    "revenue", "gross_profit", "operating_income", "net_income", "eps_diluted",
    "interest_expense", "operating_cash_flow", "capex", "dividends_paid",
    "buybacks", "stock_based_comp",
}


class EdgarProvider:
    name = "edgar"

    def __init__(self) -> None:
        ua = get_settings().edgar_user_agent
        if not ua:
            raise CapabilityUnavailable(
                "SEC EDGAR requires a User-Agent ('name email'); set "
                "VIGIL_EDGAR_USER_AGENT to enable this adapter."
            )
        self._http = HttpFetcher(base_headers={"User-Agent": ua}, min_interval_s=0.5)
        self._cik_cache: dict[str, int] | None = None

    def _cik(self, ticker: str) -> int:
        if self._cik_cache is None:
            body, _, _ = self._http.get(TICKER_MAP_URL)
            data = json.loads(body)
            self._cik_cache = {
                row["ticker"].upper(): int(row["cik_str"]) for row in data.values()
            }
        cik = self._cik_cache.get(ticker.upper())
        if cik is None:
            raise CapabilityUnavailable(f"EDGAR: no CIK found for ticker {ticker}")
        return cik

    def fetch_fundamentals(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        cik = self._cik(ticker)
        url = FACTS_URL.format(cik=cik)
        body, retrieved_at, _ = self._http.get(url)
        try:
            facts = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"EDGAR: invalid JSON for {ticker}: {exc}") from exc
        gaap: dict[str, Any] = facts.get("facts", {}).get("us-gaap", {})

        # (period_end, period_type) -> {field: value}, filed date, accession.
        periods: dict[tuple[date, str], dict[str, Any]] = {}
        for field, concepts in CONCEPTS.items():
            for concept in concepts:
                node = gaap.get(concept)
                if not node:
                    continue
                units = node.get("units", {})
                entries = units.get("USD") or units.get("USD/shares") or units.get("shares") or []
                for e in entries:
                    ptype = self._period_type(e, field)
                    if ptype is None:
                        continue
                    try:
                        pe = date.fromisoformat(e["end"])
                        filed = date.fromisoformat(e["filed"])
                    except (KeyError, ValueError):
                        continue
                    if not (start <= filed <= end):
                        continue
                    key = (pe, ptype)
                    slot = periods.setdefault(
                        key, {"fields": {}, "filed": filed, "accn": e.get("accn", "")}
                    )
                    if field not in slot["fields"]:
                        slot["fields"][field] = float(e["val"])
                        slot["filed"] = max(slot["filed"], filed)
                break  # first concept with data wins for this field

        records: list[p.FundamentalPayload] = []
        for (pe, ptype), slot in sorted(periods.items()):
            fields = slot["fields"]
            if "revenue" not in fields and "net_income" not in fields:
                continue  # too sparse to be a usable report
            records.append(
                p.FundamentalPayload(
                    ticker=ticker,
                    period_end=pe,
                    period_type=ptype,
                    published_at=datetime.combine(slot["filed"], time(16, 0)),
                    currency="USD",
                    fields=fields,
                    source_reference=(
                        f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                        f"&CIK={cik:010d}&type=10&dateb=&owner=include&count=10"
                        if not slot["accn"]
                        else f"https://www.sec.gov/Archives/edgar/data/{cik}/{slot['accn'].replace('-', '')}"
                    ),
                    shares_outstanding=fields.get("shares_diluted"),
                )
            )
        return p.ProviderFetchResult(
            records=records,
            raw=body[:2_000_000],
            endpoint=url,
            retrieved_at=retrieved_at,
            warnings=["EDGAR core-concept mapping only; segment data ignored"],
        )

    @staticmethod
    def _period_type(entry: dict, field: str) -> str | None:
        """Q or A from form type + duration; None to skip the entry."""
        form = entry.get("form", "")
        if form not in ("10-Q", "10-K", "10-K/A", "10-Q/A"):
            return None
        if field not in FLOW_FIELDS:
            # Instant (balance-sheet) facts: type by the carrying form.
            return "Q" if form.startswith("10-Q") else "A"
        start_s, end_s = entry.get("start"), entry.get("end")
        if not start_s or not end_s:
            return None
        try:
            days = (date.fromisoformat(end_s) - date.fromisoformat(start_s)).days
        except ValueError:
            return None
        if 80 <= days <= 100:
            return "Q"
        if 350 <= days <= 380:
            return "A"
        return None  # YTD or irregular spans are skipped in this adapter

    def health_check(self) -> tuple[bool, str]:
        try:
            self._cik("AAPL")
            return True, "ticker map reachable"
        except Exception as exc:
            return False, str(exc)
