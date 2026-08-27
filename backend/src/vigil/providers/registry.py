"""Provider registry: resolves configured adapter per capability.

Also provides the shared HTTP fetch helper with caching, retries,
rate-limit handling and raw-payload capture that real adapters use.
"""

from __future__ import annotations

import random
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from vigil.config import get_settings
from vigil.providers.base import CapabilityUnavailable, ProviderError

CAPABILITIES = (
    "reference", "prices", "fundamentals", "estimates", "news",
    "ownership", "macro", "options",
)


def _build(provider_name: str) -> Any:
    if provider_name == "synthetic":
        from vigil.providers.synthetic import SyntheticProvider

        return SyntheticProvider()
    if provider_name == "edgar":
        from vigil.providers.edgar import EdgarProvider

        return EdgarProvider()
    if provider_name == "stooq":
        from vigil.providers.stooq import StooqProvider

        return StooqProvider()
    if provider_name == "static":
        from vigil.providers.static_universe import StaticUniverseProvider

        return StaticUniverseProvider()
    if provider_name == "tiingo":
        from vigil.providers.tiingo import TiingoProvider

        return TiingoProvider()
    if provider_name == "eodhd":
        from vigil.providers.eodhd import EodhdProvider

        return EodhdProvider()
    raise CapabilityUnavailable(f"No adapter registered under name '{provider_name}'")


_cache: dict[str, Any] = {}


def get_provider(capability: str) -> Any:
    """Return the configured adapter instance for a capability."""
    settings = get_settings()
    mapping = {
        "reference": settings.provider_reference or settings.provider_price,
        "prices": settings.provider_price,
        "fundamentals": settings.provider_fundamentals,
        "estimates": settings.provider_estimates,
        "news": settings.provider_news,
        "ownership": settings.provider_news,
        "macro": settings.provider_macro,
        "options": settings.provider_options,
    }
    if capability not in mapping:
        raise ValueError(f"Unknown capability '{capability}'")
    name = mapping[capability]
    if not name:
        raise CapabilityUnavailable(
            f"No provider configured for '{capability}'. "
            f"Set VIGIL_PROVIDER_{capability.upper()} to enable it."
        )
    key = f"{capability}:{name}"
    if key not in _cache:
        _cache[key] = _build(name)
    return _cache[key]


def reset_provider_cache() -> None:
    _cache.clear()


# ---------------------------------------------------------------------------
# Shared HTTP helper for real adapters
# ---------------------------------------------------------------------------


class HttpFetcher:
    """GET with exponential-backoff retries, 429 handling, and timing."""

    def __init__(
        self,
        base_headers: dict[str, str] | None = None,
        max_retries: int = 4,
        timeout: float = 30.0,
        min_interval_s: float = 0.0,
    ) -> None:
        self._headers = base_headers or {}
        self._max_retries = max_retries
        self._timeout = timeout
        self._min_interval_s = min_interval_s
        self._last_request_at = 0.0

    def get(self, url: str, params: dict | None = None) -> tuple[str, datetime, float]:
        """Returns (body, retrieved_at, latency_ms). Raises ProviderError."""
        delay = 1.0
        last_err: Exception | None = None
        for attempt in range(self._max_retries + 1):
            self._respect_rate_limit()
            started = time.monotonic()
            try:
                resp = httpx.get(
                    url, params=params, headers=self._headers, timeout=self._timeout
                )
                latency = (time.monotonic() - started) * 1000
                self._last_request_at = time.monotonic()
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", delay))
                    time.sleep(min(retry_after, 60.0))
                    delay *= 2
                    continue
                if resp.status_code >= 500:
                    raise ProviderError(f"{url}: HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    # 4xx (other than 429) will not heal on retry. Carry a
                    # body snippet — provider block pages explain themselves.
                    snippet = " ".join(resp.text[:160].split())
                    raise CapabilityUnavailable(
                        f"{url}: HTTP {resp.status_code}"
                        + (f" — {snippet}" if snippet else "")
                    )
                return resp.text, datetime.now(UTC), latency
            except CapabilityUnavailable:
                raise
            except Exception as exc:
                last_err = exc
                if attempt < self._max_retries:
                    time.sleep(delay + random.uniform(0, 0.25))
                    delay *= 2
        raise ProviderError(f"GET {url} failed after retries: {last_err}")

    def _respect_rate_limit(self) -> None:
        if self._min_interval_s <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_interval_s:
            time.sleep(self._min_interval_s - elapsed)
