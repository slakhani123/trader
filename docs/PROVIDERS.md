# Data providers

## Model

Every capability (prices, fundamentals, estimates, news, ownership, macro,
options) is served by a replaceable adapter chosen via
`VIGIL_PROVIDER_<CAPABILITY>` env vars. Core logic only sees the protocols
in `providers/base.py` and the normalised store; no vendor type leaks past
`data/ingest.py`.

| Capability   | Shipped adapters                    | Notes |
|--------------|-------------------------------------|-------|
| reference    | `synthetic`, `static`, *(eodhd: declines by design)* | `static` = editable universe.yml list — the recommended real-data universe |
| prices       | `synthetic`, `stooq`, `tiingo`, `eodhd` | stooq: split-adjusted EOD, no actions, GBX→GBP handled; tiingo: free-key US raw EOD + splits/dividends; eodhd: raw EOD + splits/dividends |
| fundamentals | `synthetic`, `edgar`, `eodhd`       | edgar: US only, core us-gaap, PIT via `filed`; eodhd: global incl. LSE, PIT via `filing_date` |
| estimates    | `synthetic`, `eodhd`                | eodhd: snapshot consensus + 30/90d-ago trend fields (see REAL_DATA.md caveats) |
| news         | `synthetic`, `eodhd`                | eodhd: untyped news → conservatively weighted as commentary |
| ownership    | `synthetic`                         | short interest / insiders (not mapped in eodhd v1) |
| macro        | `synthetic`, `stooq`, `tiingo`, `eodhd` | stooq: VIX only; tiingo: FX only; eodhd: VIX + US/UK CPI; rates/spreads honestly unavailable |
| options      | *(none)*                            | reported "unavailable" until configured |

Real-data setup walkthroughs (free and EODHD): **docs/REAL_DATA.md**.

The Data Health page (and `vigil health`) reports each capability's
configured/ok state honestly — an unconfigured capability shows as
unavailable rather than silently degrading scores (confidence penalties
handle missing inputs).

## Ingest guarantees

Raw responses are stored in `raw_payloads` (lineage) before normalisation.
Ingest validates (OHLC coherence, non-negative volume, publication-after-
period-end for fundamentals — violations are rejected and logged), dedupes
(append-only observation tables), and never mutates history: a restatement
is a new row with its own `published_at`.

## Point-in-time rules a new adapter MUST follow

1. `published_at` = when the information became public. Filing time for
   fundamentals, article time for news, snapshot date for consensus. Never
   the fetch time.
2. Deliver raw, unadjusted bars if the vendor offers them; if only
   adjusted bars exist (stooq), say so in `warnings` — backtests over
   split periods need an actions-capable source.
3. Raise `CapabilityUnavailable` for anything the vendor does not supply.
4. Respect the vendor's terms; no scraping in violation of ToS. Keys live
   in env vars only (`.env`, never committed).

## Adding a vendor (walkthrough)

1. Copy `providers/template.py` → `providers/<vendor>.py`; implement the
   capabilities it truly has using `HttpFetcher` (retries, backoff, 429
   handling, pacing).
2. Add any key to `config.Settings` (e.g. `vendor_api_key: str = ""`).
3. Register the name in `providers/registry._build`.
4. Set `VIGIL_PROVIDER_<CAPABILITY>=<vendor>` in `.env`.
5. Add a contract test under `tests/providers/` using a canned fixture
   response (no live network in tests).
6. Run `vigil seed && vigil health`.

## Recommended real-data stack (requires licences/keys)

- Prices + actions + delistings: Polygon.io, Tiingo, or EODHD (all offer
  UK equities on paid tiers; EODHD covers LSE well).
- Fundamentals: EDGAR (free, US) as shipped; SharadarCore/EODHD for
  survivorship-free history incl. delisted names.
- Estimates/targets: Refinitiv IBES, FactSet, or Finnhub (budget option).
- News: vendor with entity tagging + publish timestamps (Finnhub, Benzinga,
  Marketaux).
- Short interest: FINRA (US, free), FCA disclosures (UK). Insiders: EDGAR
  Form 4 / RNS director dealings.
- Macro: FRED (free API key), BoE/ONS for UK series.
- Options (optional capability): Polygon/ORATS.

## UK quirks handled

LSE quotes arrive in pence (GBX); adapters convert to GBP at ingest (the
synthetic world quotes GBP directly — documented in ASSUMPTIONS.md). UK
reporting is often semi-annual; the schema is period-type aware, and the
synthetic world's quarterly simplification is documented.
