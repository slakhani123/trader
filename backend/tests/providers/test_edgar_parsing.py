"""EDGAR companyfacts parsing tests on a realistic canned payload (no network).

The fixture mirrors the real https://data.sec.gov/api/xbrl/companyfacts shape:
duration facts carry start/end/form/filed, instant (balance-sheet) facts have
no start, 10-Qs tag BOTH the discrete quarter and the fiscal-YTD span for the
same period end, and history reaches back beyond any requested window.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from vigil.config import reset_settings_cache
from vigil.providers.base import CapabilityUnavailable
from vigil.providers.edgar import EdgarProvider

TICKER_MAP = {
    "0": {"cik_str": 320193, "ticker": "TCORP", "title": "Test Corp"},
}

FACTS = {
    "cik": 320193,
    "entityName": "Test Corp",
    "facts": {
        "us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "units": {"USD": [
                    # FY2020 10-K — filed before the requested window: excluded.
                    {"start": "2019-09-29", "end": "2020-09-26", "val": 274.5e9,
                     "fy": 2020, "fp": "FY", "form": "10-K", "filed": "2020-10-30",
                     "accn": "0000320193-20-000096"},
                    # FY2025 10-K annual span (364 days on a 52/53-week calendar).
                    {"start": "2024-09-29", "end": "2025-09-27", "val": 400.0e9,
                     "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31",
                     "accn": "0000320193-25-000106"},
                    # Q1 FY2026 10-Q, discrete 91-day quarter.
                    {"start": "2025-09-28", "end": "2025-12-27", "val": 124.3e9,
                     "fy": 2026, "fp": "Q1", "form": "10-Q", "filed": "2026-01-30",
                     "accn": "0000320193-26-000008"},
                    # Q2 FY2026 10-Q tags the six-month YTD span FIRST (real
                    # filings do) — the parser must skip it, not book it as Q2.
                    {"start": "2025-09-28", "end": "2026-03-28", "val": 220.0e9,
                     "fy": 2026, "fp": "Q2", "form": "10-Q", "filed": "2026-05-01",
                     "accn": "0000320193-26-000057"},
                    # ...and the discrete Q2 quarter with the same period end.
                    {"start": "2025-12-28", "end": "2026-03-28", "val": 95.7e9,
                     "fy": 2026, "fp": "Q2", "form": "10-Q", "filed": "2026-05-01",
                     "accn": "0000320193-26-000057"},
                    # An 8-K tagged duplicate — wrong form, must be ignored.
                    {"start": "2025-12-28", "end": "2026-03-28", "val": 1.0,
                     "fy": 2026, "fp": "Q2", "form": "8-K", "filed": "2026-05-01",
                     "accn": "0000320193-26-000060"},
                ]}
            },
            "NetIncomeLoss": {
                "units": {"USD": [
                    {"start": "2024-09-29", "end": "2025-09-27", "val": 100.0e9,
                     "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31",
                     "accn": "0000320193-25-000106"},
                    {"start": "2025-09-28", "end": "2025-12-27", "val": 36.3e9,
                     "fy": 2026, "fp": "Q1", "form": "10-Q", "filed": "2026-01-30",
                     "accn": "0000320193-26-000008"},
                ]}
            },
            "EarningsPerShareDiluted": {
                "units": {"USD/shares": [
                    {"start": "2025-09-28", "end": "2025-12-27", "val": 2.40,
                     "fy": 2026, "fp": "Q1", "form": "10-Q", "filed": "2026-01-30",
                     "accn": "0000320193-26-000008"},
                ]}
            },
            # Instant facts: no "start" key, typed by the carrying form.
            "Assets": {
                "units": {"USD": [
                    {"end": "2025-09-27", "val": 331.5e9,
                     "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31",
                     "accn": "0000320193-25-000106"},
                    {"end": "2025-12-27", "val": 344.1e9,
                     "fy": 2026, "fp": "Q1", "form": "10-Q", "filed": "2026-01-30",
                     "accn": "0000320193-26-000008"},
                ]}
            },
            "WeightedAverageNumberOfDilutedSharesOutstanding": {
                "units": {"shares": [
                    {"start": "2025-09-28", "end": "2025-12-27", "val": 15.1e9,
                     "fy": 2026, "fp": "Q1", "form": "10-Q", "filed": "2026-01-30",
                     "accn": "0000320193-26-000008"},
                ]}
            },
        }
    },
}

WINDOW = (date(2025, 6, 1), date(2026, 8, 27))


class FakeHttp:
    def get(self, url: str, params: dict | None = None):
        if "company_tickers" in url:
            return json.dumps(TICKER_MAP), datetime.now(UTC), 5.0
        if "companyfacts" in url:
            return json.dumps(FACTS), datetime.now(UTC), 5.0
        raise AssertionError(f"unexpected URL {url}")


@pytest.fixture()
def provider(monkeypatch) -> EdgarProvider:
    monkeypatch.setenv("VIGIL_EDGAR_USER_AGENT", "Test Person test@example.com")
    reset_settings_cache()
    prov = EdgarProvider()
    reset_settings_cache()
    prov._http = FakeHttp()  # type: ignore[assignment]
    return prov


def by_period(records) -> dict[tuple[date, str], object]:
    return {(r.period_end, r.period_type): r for r in records}


class TestParsing:
    def test_quarters_and_annuals_extracted(self, provider):
        result = provider.fetch_fundamentals("TCORP", *WINDOW)
        periods = by_period(result.records)
        assert (date(2025, 12, 27), "Q") in periods
        assert (date(2026, 3, 28), "Q") in periods
        assert (date(2025, 9, 27), "A") in periods

    def test_ytd_span_skipped_not_booked_as_quarter(self, provider):
        result = provider.fetch_fundamentals("TCORP", *WINDOW)
        q2 = by_period(result.records)[(date(2026, 3, 28), "Q")]
        # Discrete quarter (95.7bn), NOT the six-month YTD figure (220bn).
        assert q2.fields["revenue"] == pytest.approx(95.7e9)

    def test_wrong_form_and_out_of_window_filings_excluded(self, provider):
        result = provider.fetch_fundamentals("TCORP", *WINDOW)
        periods = by_period(result.records)
        assert (date(2020, 9, 26), "A") not in periods  # filed 2020, before window
        # The 8-K duplicate (val=1.0) must not have overwritten the 10-Q value.
        assert periods[(date(2026, 3, 28), "Q")].fields["revenue"] != 1.0

    def test_instant_facts_join_their_period(self, provider):
        result = provider.fetch_fundamentals("TCORP", *WINDOW)
        periods = by_period(result.records)
        q1 = periods[(date(2025, 12, 27), "Q")]
        fy = periods[(date(2025, 9, 27), "A")]
        assert q1.fields["total_assets"] == pytest.approx(344.1e9)
        assert fy.fields["total_assets"] == pytest.approx(331.5e9)

    def test_point_in_time_published_at_is_filing_date(self, provider):
        result = provider.fetch_fundamentals("TCORP", *WINDOW)
        for r in result.records:
            assert r.published_at.date() >= r.period_end  # no look-ahead
        q1 = by_period(result.records)[(date(2025, 12, 27), "Q")]
        assert q1.published_at.date() == date(2026, 1, 30)

    def test_eps_and_shares_units_mapped(self, provider):
        q1 = by_period(provider.fetch_fundamentals("TCORP", *WINDOW).records)[
            (date(2025, 12, 27), "Q")
        ]
        assert q1.fields["eps_diluted"] == pytest.approx(2.40)
        assert q1.shares_outstanding == pytest.approx(15.1e9)

    def test_unknown_ticker_raises_capability_unavailable(self, provider):
        with pytest.raises(CapabilityUnavailable, match="no CIK"):
            provider.fetch_fundamentals("VOD.L", *WINDOW)
