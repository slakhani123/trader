"""Stooq adapter parsing tests on canned responses (no network)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from vigil.providers.stooq import StooqProvider

BARS_CSV = """Date,Open,High,Low,Close,Volume
2026-08-21,100.0,102.0,99.0,101.5,1200000
2026-08-22,101.5,103.0,101.0,102.0,900000
"""

GBX_CSV = """Date,Open,High,Low,Close,Volume
2026-08-21,7250.0,7300.0,7200.0,7280.0,500000
"""

FX_CSV = """Date,Open,High,Low,Close,Volume
2026-08-21,0.790,0.792,0.788,0.791,0
"""


class FakeHttp:
    def __init__(self, body_by_symbol: dict[str, str]) -> None:
        self.body_by_symbol = body_by_symbol
        self.calls: list[dict] = []

    def get(self, url: str, params: dict | None = None):
        self.calls.append(params or {})
        symbol = (params or {}).get("s", "")
        body = self.body_by_symbol.get(symbol, "No data")
        return body, datetime.now(UTC), 10.0


def provider(bodies: dict[str, str]) -> StooqProvider:
    sp = StooqProvider()
    sp._http = FakeHttp(bodies)  # type: ignore[assignment]
    return sp


class TestSymbols:
    def test_mapping(self):
        sp = StooqProvider()
        assert sp._symbol("AAPL") == "aapl.us"
        assert sp._symbol("VOD.L") == "vod.uk"
        assert sp._symbol("^SPX") == "^spx"
        assert sp._symbol("^UKX") == "^ukx"


class TestBars:
    def test_us_bars(self):
        sp = provider({"aapl.us": BARS_CSV})
        result = sp.fetch_bars("AAPL", date(2026, 8, 1), date(2026, 8, 25))
        assert len(result.records) == 2
        bar = result.records[0]
        assert bar.currency == "USD" and bar.close == 101.5
        assert any("split-adjusted" in w for w in result.warnings)

    def test_uk_bars_convert_gbx_to_gbp(self):
        sp = provider({"shel.uk": GBX_CSV})
        result = sp.fetch_bars("SHEL.L", date(2026, 8, 1), date(2026, 8, 25))
        bar = result.records[0]
        assert bar.currency == "GBP"
        assert bar.close == pytest.approx(72.80)  # 7280 pence -> £72.80

    def test_no_data_raises_actionable_error(self):
        from vigil.providers.base import CapabilityUnavailable

        sp = provider({})
        with pytest.raises(CapabilityUnavailable, match="page instead of data"):
            sp.fetch_bars("ZZZZ", date(2026, 8, 1), date(2026, 8, 25))


class TestFxAndMacro:
    def test_fx_pairs(self):
        sp = provider({"usdgbp": FX_CSV})
        result = sp.fetch_fx([("USD", "GBP"), ("EUR", "GBP")], date(2026, 8, 1), date(2026, 8, 25))
        assert len(result.records) == 1
        fx = result.records[0]
        assert (fx.base_ccy, fx.quote_ccy, fx.rate) == ("USD", "GBP", 0.791)
        assert any("EUR/GBP" in w for w in result.warnings)

    def test_macro_vix_only(self):
        sp = provider({"^vix": BARS_CSV})
        result = sp.fetch_macro(["vix", "us_policy_rate"], date(2026, 8, 1), date(2026, 8, 25))
        assert {r.series_id for r in result.records} == {"vix"}
        assert any("us_policy_rate" in w for w in result.warnings)
        assert all(r.published_at.date() == r.obs_date for r in result.records)
