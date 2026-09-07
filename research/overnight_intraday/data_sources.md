# Data provenance

The research container has no market-data network access (egress allowlist
covers package registries and GitHub only), so all price data comes from
public GitHub repositories cloned read-only. None of the raw data is
committed to this repo; clone the sources below to reproduce.

## Primary sources

| source | span | layout | adjustment |
|---|---|---|---|
| `scienclick/stocks` (in-git mirror of B. Marjanovic's Kaggle "Huge Stock Market Dataset") | 1962 → 2017-11-10 | `data/{Stocks,ETFs}/<t>.us.txt`, `Date,Open,High,Low,Close,Volume,OpenInt` | split+dividend adjusted OHLC |
| `yennanliu/finance_data` | 2016-09-06 → 2026-09-04 (updated nightly by GitHub Actions from yfinance) | `data/prices/<t>.csv`, `date,open,high,low,close,volume,div,split` | split-adjusted; dividends as cash column on ex-date (credited to the overnight leg by `extend_study.py`) |
| `KartikPahadiya/stock-market-prediction` | 2017-01-03 → 2026-08-17 | `data/raw/stocks/<T>.csv`, `Date,Close,High,Low,Open,Volume` | split+dividend adjusted (yfinance auto_adjust) — used for AAPL, GOOGL, JNJ, JPM, PG, XOM |
| `asrith-306/market-analytics-toolkit` | 2016-08-08 → 2026-08-07 | `datasets/SPY.csv` | split+dividend adjusted — used for SPY |

## Cross-checks performed

* `eliangcs/pystock-data` (independent crawler, captured near-contemporaneously
  from Yahoo pre-open each day, 2009→2017-03): MU rows for 2017-03-30/31
  match `finance_data` OHLC exactly.
* Marjanovic vs finance_data overlap (2016-09-06→2017-11-10, 299 common
  days): median |daily close-to-close return difference| = 0.0 bps,
  p95 = 5.8 bps, max = 18 bps (MU). SPY overlap vs asrith: p95 = 1.5 bps.
* MU close 2026-09-04 in finance_data = $1,016.59; live quote services
  report the same value for that date, and the +6.10% move on 2026-09-04
  matches financial-news coverage.
* An independent stdlib re-implementation (written without reading the
  study code) reproduced the headline MU statistics from the same raw
  files — see `verification.md`.

## Known data limitations

* **Opens before ~1995 are unreliable everywhere in retail data** (CRSP
  itself has no opens 1962–1992). We detect and trim years where >25% of
  days have open==close (synthetic opens) or >40% have open==prev-close
  (stale opens). Residual staleness in the fraction-tick era (pre-2001)
  biases the overnight leg DOWN, i.e. against the hypothesis under test.
* Marjanovic prices are dividend-adjusted, so dividends accrue to the
  overnight leg implicitly (economically correct: the overnight holder
  earns the dividend). MU paid no dividends before 2021.
* finance_data is dividend-UNadjusted; `extend_study.py` adds the ex-date
  cash dividend to the overnight leg explicitly, with a 5%-of-price guard
  against mis-scaled rows.
* All recent sources are yfinance-lineage; a bad Yahoo print would flow
  into all of them. Mitigations: pystock-data (contemporaneous capture)
  agreement on overlap, and the live-quote check on the last bar.
