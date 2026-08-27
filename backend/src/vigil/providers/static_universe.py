"""Static universe provider: the tradeable universe comes from an editable
YAML file instead of a vendor screener.

This is the recommended reference source for real-data mode — free price
vendors (stooq) and filings sources (EDGAR) have no universe endpoint, and
a hand-picked list keeps the tool's coverage deliberate. Copy
``universe.example.yml`` to ``universe.yml`` (or point ``VIGIL_UNIVERSE_FILE``
elsewhere) and edit freely; the next ``vigil seed`` picks it up.

File schema (one entry per instrument)::

    instruments:
      - ticker: AAPL          # required. UK/LSE names end .L (e.g. VOD.L)
        name: Apple Inc.      # required
        market: US            # required: US | UK
        sector: Technology    # required (peer grouping)
        industry: Consumer Electronics   # optional
        exchange: NASDAQ      # optional (defaults NYSE / LSE by market)
        currency: USD         # optional (defaults USD / GBP by market)
        security_type: common # optional: common | index
      - ticker: ^SPX          # benchmark index for the US market
        name: S&P 500
        market: US
        sector: ""            # empty sector = the market benchmark
        security_type: index

Include one ``security_type: index`` entry with an empty sector per market —
it becomes that market's benchmark for relative-strength and regime work.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from vigil.config import get_settings
from vigil.providers import base as p
from vigil.providers.base import CapabilityUnavailable

_DEFAULT_EXCHANGE = {"US": "NYSE", "UK": "LSE"}
_DEFAULT_CURRENCY = {"US": "USD", "UK": "GBP"}
_REQUIRED = ("ticker", "name", "market")


class StaticUniverseProvider:
    name = "static"

    def __init__(self, path: str | None = None) -> None:
        self._path = Path(path or get_settings().universe_file)

    def fetch_universe(self, markets: list[str]) -> p.ProviderFetchResult:
        if not self._path.exists():
            raise CapabilityUnavailable(
                f"Universe file '{self._path}' not found. Copy universe.example.yml "
                "to universe.yml (in the backend folder) and edit the company list, "
                "or set VIGIL_UNIVERSE_FILE to its location."
            )
        try:
            data = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise CapabilityUnavailable(
                f"Universe file '{self._path}' is not valid YAML: {exc}"
            ) from exc
        entries = data.get("instruments")
        if not isinstance(entries, list) or not entries:
            raise CapabilityUnavailable(
                f"Universe file '{self._path}' must contain a non-empty "
                "'instruments:' list (see universe.example.yml)."
            )
        records: list[p.InstrumentPayload] = []
        warnings: list[str] = []
        for i, raw in enumerate(entries):
            if not isinstance(raw, dict):
                warnings.append(f"entry {i + 1} skipped: not a mapping")
                continue
            missing = [k for k in _REQUIRED if not raw.get(k)]
            if missing:
                warnings.append(
                    f"entry {i + 1} ({raw.get('ticker', '?')}) skipped: missing {missing}"
                )
                continue
            market = str(raw["market"]).upper()
            if market not in markets:
                continue
            sec_type = str(raw.get("security_type", "common")).lower()
            records.append(
                p.InstrumentPayload(
                    ticker=str(raw["ticker"]).upper(),
                    exchange=str(raw.get("exchange") or _DEFAULT_EXCHANGE.get(market, "NYSE")),
                    market=market,
                    name=str(raw["name"]),
                    sector=str(raw.get("sector") or ""),
                    industry=str(raw.get("industry") or ""),
                    currency=str(raw.get("currency") or _DEFAULT_CURRENCY.get(market, "USD")),
                    security_type=sec_type,
                    is_shell=bool(raw.get("is_shell", False)),
                    delisted_at=(
                        date.fromisoformat(str(raw["delisted_at"]))
                        if raw.get("delisted_at")
                        else None
                    ),
                )
            )
        for market in markets:
            if not any(r.security_type == "index" and r.market == market and r.sector == ""
                       for r in records) and any(r.market == market for r in records):
                warnings.append(
                    f"no benchmark index defined for market {market} (add a "
                    "security_type: index entry with an empty sector) — relative "
                    "strength and regime classification will be degraded"
                )
        return p.ProviderFetchResult(
            records=records,
            endpoint=f"file://{self._path}",
            warnings=warnings,
        )

    def health_check(self) -> tuple[bool, str]:
        try:
            result = self.fetch_universe(get_settings().universe.markets)
            return True, f"{len(result.records)} instruments in {self._path}"
        except CapabilityUnavailable as exc:
            return False, str(exc)
