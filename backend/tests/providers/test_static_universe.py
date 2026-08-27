"""Static (YAML) universe provider contract tests."""

from __future__ import annotations

import pytest

from vigil.providers.base import CapabilityUnavailable
from vigil.providers.static_universe import StaticUniverseProvider

GOOD = """
instruments:
  - {ticker: "^SPX", name: "S&P 500", market: US, sector: "", security_type: index}
  - {ticker: AAPL, name: Apple, market: US, sector: Technology, industry: Hardware, exchange: NASDAQ}
  - {ticker: vod.l, name: Vodafone, market: UK, sector: Technology}
  - {ticker: BADROW, market: US}          # missing name -> skipped with warning
  - {ticker: TEST, name: Test, market: DE, sector: X}   # market not requested
"""


def test_parses_entries_with_defaults(tmp_path):
    f = tmp_path / "u.yml"
    f.write_text(GOOD)
    result = StaticUniverseProvider(str(f)).fetch_universe(["US", "UK"])
    by_ticker = {r.ticker: r for r in result.records}
    assert set(by_ticker) == {"^SPX", "AAPL", "VOD.L"}
    assert by_ticker["AAPL"].exchange == "NASDAQ"
    assert by_ticker["VOD.L"].exchange == "LSE"  # market default
    assert by_ticker["VOD.L"].currency == "GBP"
    assert by_ticker["^SPX"].security_type == "index"
    assert any("BADROW" in w or "entry 4" in w for w in result.warnings)


def test_warns_when_market_has_no_benchmark(tmp_path):
    f = tmp_path / "u.yml"
    f.write_text(
        "instruments:\n  - {ticker: SHEL.L, name: Shell, market: UK, sector: Energy}\n"
    )
    result = StaticUniverseProvider(str(f)).fetch_universe(["UK"])
    assert any("benchmark" in w for w in result.warnings)


def test_missing_file_is_actionable(tmp_path):
    with pytest.raises(CapabilityUnavailable, match=r"universe\.example\.yml"):
        StaticUniverseProvider(str(tmp_path / "nope.yml")).fetch_universe(["US"])


def test_empty_file_rejected(tmp_path):
    f = tmp_path / "u.yml"
    f.write_text("instruments: []\n")
    with pytest.raises(CapabilityUnavailable, match="non-empty"):
        StaticUniverseProvider(str(f)).fetch_universe(["US"])
