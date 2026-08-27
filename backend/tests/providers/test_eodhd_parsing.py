"""EODHD adapter contract tests on canned fixture responses (no network).

The adapter is not verified against the live service in CI — these tests
pin the PARSING contract: symbol mapping, GBX conversion, split-ratio
parsing, quarterly-statement joining with filing-date point-in-time
publication, estimate-trend mapping, and soft-failure behaviour.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from vigil.config import reset_settings_cache
from vigil.providers.eodhd import EodhdProvider


@pytest.fixture()
def provider(monkeypatch):
    monkeypatch.setenv("VIGIL_EODHD_API_KEY", "test-key")
    reset_settings_cache()
    prov = EodhdProvider()
    reset_settings_cache()
    return prov


class FakeHttp:
    def __init__(self, body_by_path: dict[str, object]) -> None:
        self.body_by_path = body_by_path
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, params: dict | None = None):
        self.calls.append((url, params or {}))
        for key, body in self.body_by_path.items():
            if key in url:
                return json.dumps(body), datetime.now(UTC), 5.0
        return json.dumps([]), datetime.now(UTC), 5.0


def wire(provider: EodhdProvider, bodies: dict[str, object]) -> FakeHttp:
    fake = FakeHttp(bodies)
    provider._http = fake  # type: ignore[assignment]
    return fake


class TestSymbols:
    def test_mapping(self, provider):
        assert provider._symbol("AAPL") == ("AAPL.US", False)
        assert provider._symbol("VOD.L") == ("VOD.LSE", True)
        assert provider._symbol("^SPX") == ("GSPC.INDX", False)
        assert provider._symbol("^UKX") == ("FTSE.INDX", False)

    def test_requires_key(self, monkeypatch):
        from vigil.providers.base import CapabilityUnavailable

        monkeypatch.delenv("VIGIL_EODHD_API_KEY", raising=False)
        reset_settings_cache()
        with pytest.raises(CapabilityUnavailable, match="VIGIL_EODHD_API_KEY"):
            EodhdProvider()
        reset_settings_cache()


class TestBarsAndActions:
    def test_lse_bars_gbx_to_gbp(self, provider):
        wire(provider, {"eod/VOD.LSE": [
            {"date": "2026-08-21", "open": "72.0", "high": "74.0", "low": "71.0",
             "close": "73.5", "volume": 1000000},
        ]})
        result = provider.fetch_bars("VOD.L", date(2026, 8, 1), date(2026, 8, 25))
        assert result.records[0].close == pytest.approx(0.735)
        assert result.records[0].currency == "GBP"

    def test_split_ratio_and_dividends(self, provider):
        wire(provider, {
            "splits/AAPL.US": [{"date": "2024-06-10", "split": "4.000000/1.000000"}],
            "div/AAPL.US": [{"date": "2026-05-10", "value": "0.25"}],
        })
        result = provider.fetch_actions("AAPL", date(2024, 1, 1), date(2026, 8, 25))
        kinds = {r.kind: r for r in result.records}
        assert kinds["split"].factor == 4.0
        assert kinds["dividend"].amount == 0.25

    def test_api_key_attached_to_requests(self, provider):
        fake = wire(provider, {"eod/AAPL.US": []})
        provider.fetch_bars("AAPL", date(2026, 8, 1), date(2026, 8, 25))
        assert fake.calls[0][1]["api_token"] == "test-key"


class TestFundamentals:
    FIXTURE = {
        "General": {"CurrencyCode": "USD"},
        "Financials": {
            "Income_Statement": {"quarterly": {
                "2026-06-30": {"totalRevenue": "1000", "grossProfit": "400",
                                "operatingIncome": "200", "netIncome": "150",
                                "filing_date": "2026-08-05"},
                "2026-03-31": {"totalRevenue": "900", "netIncome": "120",
                                "filing_date": "2026-05-05"},
            }},
            "Balance_Sheet": {"quarterly": {
                "2026-06-30": {"totalAssets": "5000", "totalStockholderEquity": "2500",
                                "shortLongTermDebtTotal": "800", "cashAndEquivalents": "600",
                                "commonStockSharesOutstanding": "100"},
            }},
            "Cash_Flow": {"quarterly": {
                "2026-06-30": {"totalCashFromOperatingActivities": "180",
                                "capitalExpenditures": "-40"},
            }},
        },
    }

    def test_join_and_pit_publication(self, provider):
        wire(provider, {"fundamentals/MSFT.US": self.FIXTURE})
        result = provider.fetch_fundamentals("MSFT", date(2020, 1, 1), date(2026, 8, 25))
        assert len(result.records) == 2
        q2 = next(r for r in result.records if r.period_end == date(2026, 6, 30))
        assert q2.published_at.date() == date(2026, 8, 5)  # filing_date, not period end
        assert q2.fields["capex"] == 40.0  # absolute value
        assert q2.fields["eps_diluted"] == pytest.approx(1.5)  # derived NI/shares
        assert q2.shares_outstanding == 100.0

    def test_missing_filing_date_uses_conservative_lag(self, provider):
        fixture = {"Financials": {"Income_Statement": {"quarterly": {
            "2026-03-31": {"totalRevenue": "10", "netIncome": "1"},
        }}}}
        wire(provider, {"fundamentals/X.US": fixture})
        result = provider.fetch_fundamentals("X", date(2020, 1, 1), date(2026, 12, 31))
        assert result.records[0].published_at.date() == date(2026, 5, 30)  # +60d
        assert any("60-day" in w for w in result.warnings)


class TestEstimatesTargetsNewsCalendar:
    def test_estimates_from_trend(self, provider):
        wire(provider, {"fundamentals/AAPL.US": {"Trend": {
            "2026-12-31": {"date": "2026-12-31", "period": "0y",
                            "earningsEstimateAvg": "8.10", "earningsEstimateHigh": "8.60",
                            "earningsEstimateLow": "7.70",
                            "earningsEstimateNumberOfAnalysts": "30",
                            "epsTrend30daysAgo": "8.00", "epsTrend90daysAgo": "7.80",
                            "epsRevisionsUpLast30days": "12",
                            "epsRevisionsDownLast30days": "2",
                            "revenueEstimateAvg": "400000000000",
                            "revenueEstimateNumberOfAnalysts": "28"},
        }}})
        result = provider.fetch_estimates("AAPL", date(2026, 8, 25))
        eps = next(r for r in result.records if r.metric == "eps")
        assert (eps.mean, eps.mean_30d_ago, eps.up_revisions_30d) == (8.10, 8.00, 12)
        assert any(r.metric == "revenue" for r in result.records)

    def test_targets(self, provider):
        wire(provider, {"fundamentals/AAPL.US": {
            "TargetPrice": "250.5", "StrongBuy": "10", "Buy": "15", "Hold": "8",
            "Sell": "1", "StrongSell": "0",
        }})
        result = provider.fetch_targets("AAPL", date(2026, 8, 25))
        assert result.records[0].mean == 250.5
        assert result.records[0].analyst_count == 34

    def test_news_defaults_to_commentary(self, provider):
        wire(provider, {"news": [
            {"date": "2026-08-20T09:00:00+00:00", "title": "Apple ships new thing",
             "content": "...", "link": "https://example.com/1",
             "sentiment": {"polarity": 0.4}},
        ]})
        result = provider.fetch_news("AAPL", date(2026, 8, 1), date(2026, 8, 25))
        n = result.records[0]
        assert n.source_type == "market_commentary" and n.sentiment == 0.4

    def test_calendar_resolves_past_earnings(self, provider):
        wire(provider, {"calendar/earnings": {"earnings": [
            {"report_date": "2026-07-30", "actual": "2.10", "estimate": "2.00"},
            {"report_date": "2026-10-29"},
        ]}})
        result = provider.fetch_catalysts("AAPL", date(2026, 8, 25))
        past = next(r for r in result.records if r.expected_date == date(2026, 7, 30))
        future = next(r for r in result.records if r.expected_date == date(2026, 10, 29))
        assert past.resolved and "surprise +5.0%" in (past.outcome or "")
        assert not future.resolved and future.outcome is None


class TestSoftFailure:
    def test_rows_with_bad_fields_skip_not_crash(self, provider):
        wire(provider, {"eod/AAPL.US": [
            {"date": "2026-08-21", "open": "NA", "high": None, "low": "1", "close": "2"},
            {"date": "2026-08-22", "open": "1", "high": "2", "low": "0.5", "close": "1.5",
             "volume": "10"},
        ]})
        result = provider.fetch_bars("AAPL", date(2026, 8, 1), date(2026, 8, 25))
        assert len(result.records) == 1  # bad row dropped, good row kept
