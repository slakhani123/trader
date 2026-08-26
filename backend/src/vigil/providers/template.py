"""HOW TO ADD A DATA PROVIDER — annotated template.

Copy this file to ``providers/<vendor>.py``, implement the capabilities the
vendor actually supplies, register the name in ``registry._build`` and set
``VIGIL_PROVIDER_<CAPABILITY>=<vendor>``. Full walkthrough:
docs/PROVIDERS.md.

Contract checklist:
1. Return normalised payload dataclasses from ``providers/base.py``.
2. Put the raw response body into ``ProviderFetchResult.raw`` — ingest
   stores it for lineage; never pre-digest away the original.
3. Set ``published_at`` to when the information became PUBLIC (filing time,
   article time), never the fetch time. This is what keeps backtests
   honest.
4. Raise ``CapabilityUnavailable`` for anything the vendor does not supply
   — the Data Health page reports it; do not fake or silently omit.
5. Use ``HttpFetcher`` (retries, backoff, 429 handling, pacing) and put the
   API key in an env var read via ``vigil.config.Settings``.
6. Convert GBX (pence) quotes to GBP at ingest for LSE listings.
"""

from __future__ import annotations

from datetime import date

from vigil.providers import base as p
from vigil.providers.base import CapabilityUnavailable
from vigil.providers.registry import HttpFetcher


class ExampleVendorProvider:
    """Rename me. One class may implement several capability protocols."""

    name = "example_vendor"

    def __init__(self) -> None:
        # api_key = get_settings().example_vendor_api_key  # add to config.py
        # if not api_key:
        #     raise CapabilityUnavailable("EXAMPLE_VENDOR_API_KEY not set")
        self._http = HttpFetcher(min_interval_s=0.2)

    def fetch_bars(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        # body, retrieved_at, _ = self._http.get(f"https://api.vendor.com/daily/{ticker}", ...)
        # records = [p.BarPayload(...) for row in parse(body)]
        # return p.ProviderFetchResult(records=records, raw=body, endpoint=..., retrieved_at=...)
        raise CapabilityUnavailable("example vendor: not implemented — this is a template")

    def fetch_actions(self, ticker: str, start: date, end: date) -> p.ProviderFetchResult:
        raise CapabilityUnavailable("example vendor: not implemented — this is a template")

    def health_check(self) -> tuple[bool, str]:
        return False, "template provider — implement me"
