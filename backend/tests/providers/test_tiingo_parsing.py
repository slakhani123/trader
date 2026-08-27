"""Tiingo adapter contract tests on canned responses (no network)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from vigil.config import reset_settings_cache
from vigil.providers.base import CapabilityUnavailable
from vigil.providers.tiingo import TiingoProvider

DAILY = [
    {"date": "2026-08-21T00:00:00.000Z", "open": 100.0, "high": 102.0, "low": 99.0,
     "close": 101.5, "volume": 1200000, "splitFactor": 1.0, "divCash": 0.0},
    {"date": "2026-08-22T00:00:00.000Z", "open": 101.5, "high": 103.0, "low": 101.0,
     "close": 102.0, "volume": 900000, "splitFactor": 4.0, "divCash": 0.25},
]

FX = [{"date": "2026-08-21T00:00:00.000Z", "close": 0.789}]


@pytest.fixture()
def provider(monkeypatch):
    monkeypatch.setenv("VIGIL_TIINGO_API_KEY", "test-key")
    reset_settings_cache()
    prov = TiingoProvider()
    reset_settings_cache()
    return prov


class FakeHttp:
    def __init__(self, body_by_fragment: dict[str, object]) -> None:
        self.body_by_fragment = body_by_fragment
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, params: dict | None = None):
        self.calls.append((url, params or {}))
        for fragment, body in self.body_by_fragment.items():
            if fragment in url:
                return json.dumps(body), datetime.now(UTC), 5.0
        return json.dumps({"detail": "not found"}), datetime.now(UTC), 5.0


def wire(provider: TiingoProvider, bodies: dict[str, object]) -> FakeHttp:
    fake = FakeHttp(bodies)
    provider._http = fake  # type: ignore[assignment]
    return fake


class TestSymbols:
    def test_uk_and_index_refused_with_guidance(self, provider):
        with pytest.raises(CapabilityUnavailable, match="US-only"):
            provider._symbol("VOD.L")
        with pytest.raises(CapabilityUnavailable, match="SPY"):
            provider._symbol("^SPX")

    def test_share_class_mapping(self, provider):
        assert provider._symbol("BRK.B") == "brk-b"

    def test_requires_key(self, monkeypatch):
        monkeypatch.delenv("VIGIL_TIINGO_API_KEY", raising=False)
        reset_settings_cache()
        with pytest.raises(CapabilityUnavailable, match="VIGIL_TIINGO_API_KEY"):
            TiingoProvider()
        reset_settings_cache()


class TestBarsAndActions:
    def test_bars_are_raw_prices(self, provider):
        wire(provider, {"daily/aapl/prices": DAILY})
        result = provider.fetch_bars("AAPL", date(2026, 8, 1), date(2026, 8, 25))
        assert len(result.records) == 2
        assert result.records[0].close == 101.5
        assert result.records[0].currency == "USD"

    def test_actions_from_split_factor_and_div_cash(self, provider):
        wire(provider, {"daily/aapl/prices": DAILY})
        result = provider.fetch_actions("AAPL", date(2026, 8, 1), date(2026, 8, 25))
        kinds = {r.kind: r for r in result.records}
        assert kinds["split"].factor == 4.0
        assert kinds["split"].ex_date == date(2026, 8, 22)
        assert kinds["dividend"].amount == 0.25

    def test_bars_then_actions_reuses_one_request(self, provider):
        fake = wire(provider, {"daily/aapl/prices": DAILY})
        provider.fetch_bars("AAPL", date(2026, 8, 1), date(2026, 8, 25))
        provider.fetch_actions("AAPL", date(2026, 8, 1), date(2026, 8, 25))
        assert len(fake.calls) == 1  # memoised

    def test_error_payload_is_actionable(self, provider):
        wire(provider, {"daily/zzzz/prices": {"detail": "Ticker not found"}})
        with pytest.raises(CapabilityUnavailable, match="Ticker not found"):
            provider.fetch_bars("ZZZZ", date(2026, 8, 1), date(2026, 8, 25))


class TestFxAndMacro:
    def test_fx(self, provider):
        wire(provider, {"fx/usdgbp/prices": FX})
        result = provider.fetch_fx([("USD", "GBP")], date(2026, 8, 1), date(2026, 8, 25))
        assert result.records[0].rate == 0.789

    def test_macro_honestly_empty(self, provider):
        result = provider.fetch_macro(["vix"], date(2026, 8, 1), date(2026, 8, 25))
        assert result.records == []
        assert any("vix" in w for w in result.warnings)
