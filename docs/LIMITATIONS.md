# Limitations — what is mocked, simplified, or unavailable in v1

Honest inventory, per the brief's requirement to label anything mocked.

## Data

- **Demo dataset is synthetic.** All 22 issuers are fictional; prices,
  fundamentals, estimates, news, catalysts, ownership, macro and FX are
  deterministically generated (`providers/synthetic/`). Real adapters ship
  for SEC EDGAR fundamentals and Stooq EOD prices; everything else needs a
  licensed vendor (docs/PROVIDERS.md).
- **Options-derived data (IV, skew, unusual activity): unavailable.** The
  capability and schema exist; no provider is configured, and Data Health
  reports it as such.
- **Earnings-call transcripts: not ingested** (licensing); schema supports
  them as news items.
- **The EODHD adapter is built against documented API shapes but has not
  been run against the live service** (no key in the build environment).
  Contract tests pin the parsing on fixtures; parsers fail soft into Data
  Health warnings. Its estimates/targets are current snapshots (PIT history
  accumulates only from your own daily ingests), and short interest /
  insiders are not mapped.
- **Stooq prices are split-adjusted and carry no corporate actions** — the
  adapter warns; pair with an actions-capable vendor for real backtests.
- **EDGAR adapter maps a core us-gaap concept set only**; segment data,
  non-calendar fiscal years and unusual taxonomies are simplified; UK
  issuer filings (RNS) are not parsed in v1.
- **Historical index constituents**: the demo universe is stable; dated
  universe membership needs a constituents source before real-data
  backtests can claim full survivorship control at the index level
  (delisted-name handling itself IS implemented and tested).
- **UK reporting cadence** simplified to quarterly in the synthetic world;
  UK quotes generated in GBP rather than GBX (real adapters convert).

## Analytics

- Sentiment values on synthetic news are generated labels; the sentiment
  engine aggregates deterministically but no NLP model ships in v1.
- The "priced-in" catalyst measure and the spread estimate are labelled
  heuristics (docs/FORMULAS.md).
- Scoring weights v1.0.0 are research-informed starting points; the
  calibration loop exists but no out-of-sample calibration has been run on
  real data yet — confidence carries a permanent −0.5 penalty until then.
- Insurer sector metrics are thin (treated close to banks with warnings).

## Platform

- Single user, single bearer token; no OIDC/multi-user (personal tool).
- Mobile push is a stub: `NotificationDelivery` supports the channel but
  no APNs/FCM integration ships. Email needs SMTP credentials. Webhook is
  fully functional.
- Docker compose is provided but was authored in a sandbox without a
  Docker daemon; the SQLite path is the verified one. CI runs the full
  test suite on every push.
- APScheduler runs in-process; a missed window while the process is down
  is visible in `job_runs` but not auto-backfilled (run `vigil scan`
  manually).
- Intraday monitoring re-uses EOD logic on the latest stored bar; true
  intraday bars need a real-time provider.

## Deliberately absent

- **Brokerage connectivity: none, by design.** There is no order type, no
  execution module, no broker API client anywhere in the codebase, and the
  UI states this. Version 1 cannot place trades.
