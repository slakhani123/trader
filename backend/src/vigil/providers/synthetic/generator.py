"""Deterministic synthetic world generator.

Everything is derived from fixed seeds — two runs produce byte-identical
data. The generator produces *raw, unadjusted* bars plus corporate actions,
point-in-time fundamentals with publication lags (and one restatement),
monthly estimate/target snapshots, news, catalysts, ownership data, macro
series and FX, all shaped like real-provider payloads.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from vigil.providers import base as p
from vigil.providers.synthetic.universe import (
    HISTORY_START,
    SEED,
    WORLD_NOW,
    StockSpec,
)

PROVIDER = "synthetic"
TAX_RATE = 0.23
DEBT_RATE = 0.055


def _rng(*key: object) -> np.random.Generator:
    digest = hashlib.sha256(("|".join(map(str, (SEED, *key)))).encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


def _dt(d: date, hour: int = 21) -> datetime:
    return datetime.combine(d, time(hour, 0))


BDAYS = pd.bdate_range(HISTORY_START, WORLD_NOW)

# ---------------------------------------------------------------------------
# Market and sector index paths
# ---------------------------------------------------------------------------

_MARKET_SEGMENTS = {
    "US": [
        (date(2020, 7, 1), date(2021, 12, 31), 0.18),
        (date(2022, 1, 1), date(2022, 10, 14), -0.30),
        (date(2022, 10, 15), date(2024, 12, 31), 0.16),
        (date(2025, 1, 1), date(2025, 4, 30), -0.18),
        (date(2025, 5, 1), WORLD_NOW, 0.13),
    ],
    "UK": [
        (date(2020, 7, 1), date(2021, 12, 31), 0.12),
        (date(2022, 1, 1), date(2022, 10, 14), -0.18),
        (date(2022, 10, 15), date(2024, 12, 31), 0.10),
        (date(2025, 1, 1), date(2025, 4, 30), -0.12),
        (date(2025, 5, 1), WORLD_NOW, 0.09),
    ],
}

_SECTOR_TILT = {
    "Technology": 0.05, "Industrials": 0.01, "Healthcare": 0.02, "Consumer": -0.01,
    "Energy": 0.00, "Financials": 0.00, "Real Estate": -0.04,
}


def market_returns(market: str) -> pd.Series:
    rng = _rng("market", market)
    drift = np.zeros(len(BDAYS))
    for start, end, annual in _MARKET_SEGMENTS[market]:
        mask = (pd.Timestamp(start) <= BDAYS) & (pd.Timestamp(end) >= BDAYS)
        drift[mask] = annual / 252.0
    noise = rng.standard_normal(len(BDAYS)) * 0.011
    return pd.Series(drift + noise, index=BDAYS)


def market_index_closes(market: str, base: float = 4000.0) -> pd.Series:
    rets = market_returns(market)
    return base * np.exp(rets.cumsum())


def sector_index_closes(market: str, sector: str) -> pd.Series:
    rng = _rng("sector", market, sector)
    mkt = market_returns(market)
    tilt = _SECTOR_TILT.get(sector, 0.0) / 252.0
    noise = rng.standard_normal(len(BDAYS)) * 0.006
    rets = 0.85 * mkt + tilt + noise
    return pd.Series(1000.0 * np.exp(rets.cumsum()), index=BDAYS)


# ---------------------------------------------------------------------------
# Per-stock world
# ---------------------------------------------------------------------------


class StockWorld:
    """All generated data for one ticker (computed once, deterministic)."""

    def __init__(self, spec: StockSpec) -> None:
        self.spec = spec
        self._build_prices()
        self._build_fundamentals()
        self._build_estimates_targets()
        self._build_events()
        self._build_ownership()

    # -- prices --------------------------------------------------------

    def _alpha_daily(self) -> np.ndarray:
        n = len(BDAYS)
        alpha = np.zeros(n)
        for seg in self.spec.alpha_segments:
            lo, hi = int(seg.start_frac * n), int(seg.end_frac * n)
            alpha[lo:hi] = seg.annual_alpha / 252.0
        return alpha

    def _build_prices(self) -> None:
        spec = self.spec
        rng = _rng("prices", spec.ticker)
        mkt = market_returns(spec.market).to_numpy()
        n = len(BDAYS)
        rets = self._alpha_daily() + spec.beta * mkt + rng.standard_normal(n) * spec.daily_vol
        # Discrete event jumps land on their exact dates.
        for iso, jump in spec.events.items():
            ts = pd.Timestamp(iso)
            idx = BDAYS.searchsorted(ts)
            if idx < n:
                rets[idx] += np.log1p(jump)
        econ_close = spec.base_price * np.exp(np.cumsum(rets))
        # Rescale so the path *starts* at base_price rather than drifting off it.
        econ_close = econ_close / econ_close[0] * spec.base_price

        # Acquisition: price pins near offer from announcement, bars stop at delisting.
        self.delist_ts: pd.Timestamp | None = None
        if spec.acquired:
            ann, delist, premium = spec.acquired
            ann_ts, delist_ts = pd.Timestamp(ann), pd.Timestamp(delist)
            ann_idx = BDAYS.searchsorted(ann_ts)
            if ann_idx < n:
                offer = float(econ_close[ann_idx - 1]) * (1 + premium)
                drift_noise = _rng("acq", spec.ticker).standard_normal(n - ann_idx) * 0.002
                econ_close[ann_idx:] = offer * (0.985 + np.cumsum(drift_noise) * 0.01 + 0.000)
            self.delist_ts = delist_ts

        o_noise = rng.standard_normal(n) * spec.daily_vol * 0.3
        hi_noise = np.abs(rng.standard_normal(n)) * spec.daily_vol * 0.5
        lo_noise = np.abs(rng.standard_normal(n)) * spec.daily_vol * 0.5
        econ_open = np.empty(n)
        econ_open[0] = econ_close[0]
        econ_open[1:] = econ_close[:-1] * (1 + o_noise[1:])
        econ_high = np.maximum(econ_open, econ_close) * (1 + hi_noise)
        econ_low = np.minimum(econ_open, econ_close) * (1 - lo_noise)

        vol_noise = np.exp(rng.standard_normal(n) * 0.35)
        volume = spec.volume_base * vol_noise
        if spec.archetype == "breakout":
            ramp_start = int(n * 0.85)
            ramp = np.linspace(1.0, 2.4, n - ramp_start)
            volume[ramp_start:] *= ramp
        if spec.archetype == "parabolic":
            ramp_start = int(n * 0.85)
            ramp = np.linspace(1.0, 4.0, n - ramp_start)
            volume[ramp_start:] *= ramp
        for iso, jump in spec.events.items():
            idx = BDAYS.searchsorted(pd.Timestamp(iso))
            if idx < n:
                volume[idx : idx + 3] *= 1 + 8 * abs(jump)

        # Raw (as-traded) scale: pre-split prices are FACTOR times larger,
        # pre-split volume/share counts FACTOR times smaller.
        raw_factor = np.ones(n)
        self.split_ts: pd.Timestamp | None = None
        if spec.split:
            ex_iso, factor = spec.split
            self.split_ts = pd.Timestamp(ex_iso)
            raw_factor[self.split_ts > BDAYS] = factor

        df = pd.DataFrame(
            {
                "open": econ_open * raw_factor,
                "high": econ_high * raw_factor,
                "low": econ_low * raw_factor,
                "close": econ_close * raw_factor,
                "volume": np.maximum(1000.0, volume / raw_factor),
            },
            index=BDAYS,
        )
        if self.delist_ts is not None:
            df = df.loc[df.index <= self.delist_ts]
        self.bars = df
        self.econ_close = pd.Series(econ_close, index=BDAYS)

    def econ_price_on(self, d: date) -> float:
        s = self.econ_close.loc[self.econ_close.index <= pd.Timestamp(d)]
        return float(s.iloc[-1]) if not s.empty else float(self.econ_close.iloc[0])

    def shares_econ_on(self, d: date) -> float:
        """Post-split-scale share count: drifts down with buybacks."""
        years_left = max(0.0, (WORLD_NOW - d).days / 365.25)
        return self.spec.shares * (1 + self.spec.buyback_pct) ** years_left

    def shares_raw_on(self, d: date) -> float:
        shares = self.shares_econ_on(d)
        if self.split_ts is not None and pd.Timestamp(d) < self.split_ts:
            shares /= self.spec.split[1]  # type: ignore[index]
        return shares

    # -- fundamentals ----------------------------------------------------

    @staticmethod
    def _quarter_ends() -> list[date]:
        ends = []
        d = date(2020, 9, 30)
        while d <= WORLD_NOW:
            ends.append(d)
            d = (pd.Timestamp(d) + pd.offsets.QuarterEnd(1)).date()
        return ends

    def _year_idx(self, d: date) -> int:
        return min(max(d.year - 2020, 0), 6)

    def _build_fundamentals(self) -> None:
        spec = self.spec
        rng = _rng("fund", spec.ticker)
        seasonal = {3: 0.97, 6: 0.99, 9: 1.00, 12: 1.04}
        reports: list[p.FundamentalPayload] = []
        rev = spec.base_revenue_q
        equity = spec.base_revenue_q * 4 * 0.8
        self.quarter_data: list[dict] = []
        q_ends = self._quarter_ends()
        for qi, pe in enumerate(q_ends):
            yi = self._year_idx(pe)
            g_q = (1 + spec.revenue_growth_yoy[yi]) ** 0.25
            rev = rev * g_q * (1 + rng.normal(0, 0.008))
            rev_q = rev * seasonal[pe.month]
            gm, om = spec.gross_margin[yi], spec.op_margin[yi]
            gross = rev_q * gm
            op = rev_q * om
            annual_rev = rev_q * 4
            debt = annual_rev * spec.debt_to_rev
            cash = annual_rev * spec.cash_to_rev
            interest = debt * DEBT_RATE / 4
            pretax = op - interest
            ni = pretax * (1 - TAX_RATE) if pretax > 0 else pretax
            shares_raw = self.shares_raw_on(pe)
            eps = ni / shares_raw
            accrual_drag = 1.0
            if spec.archetype in ("deteriorating", "restatement", "value_trap"):
                accrual_drag = max(0.45, 1.0 - 0.03 * max(0, qi - 8))
            ocf = ni * spec.cash_conversion * accrual_drag if ni > 0 else ni * 0.5
            capex = rev_q * spec.capex_pct_rev
            price_pe = self.econ_price_on(pe)
            mcap = price_pe * self.shares_econ_on(pe)
            dividends = mcap * spec.dividend_yield / 4
            buybacks = mcap * spec.buyback_pct / 4
            sbc = rev_q * spec.sbc_pct_rev
            dso_stretch = 1.0
            if spec.archetype in ("deteriorating", "restatement"):
                dso_stretch = 1.0 + 0.04 * max(0, qi - 10)
            receivables = rev_q * 0.65 * dso_stretch
            inventory = rev_q * (0.5 if spec.sector not in ("Financials", "Real Estate") else 0.0)
            current_liabilities = rev_q * 0.8
            current_assets = cash + receivables + inventory
            equity = equity + ni - dividends - buybacks
            total_assets = max(equity, 1.0) + debt + current_liabilities
            sector_metrics: dict[str, float] = {}
            if spec.archetype == "bank":
                sector_metrics = {
                    "net_interest_margin": 0.028 + 0.004 * (yi >= 2) - 0.002 * (yi >= 5),
                    "cet1_ratio": 0.112 + 0.001 * yi,
                    "loan_loss_provisions": rev_q * (0.04 + 0.02 * (yi == 2)),
                    "tangible_book_per_share": equity * 0.9 / shares_raw,
                }
            if spec.sector == "Real Estate":
                ffo = (ni + rev_q * 0.18) if ni > 0 else rev_q * 0.1
                nav_ps = price_pe * (1.15 if spec.archetype != "value_trap" else 1.45)
                sector_metrics = {
                    "ffo": ffo,
                    "ffo_per_share": ffo / shares_raw,
                    "occupancy": 0.94 - (0.03 * yi if spec.archetype == "value_trap" else 0.0),
                    "nav_per_share": nav_ps,
                    "ltv": 0.32 + (0.04 * yi if spec.archetype == "value_trap" else 0.0),
                }
            adj_excl = None
            if spec.archetype in ("value_trap", "deteriorating"):
                adj_excl = abs(ni) * 0.35  # persistent "one-offs"
            fields = {
                "revenue": rev_q,
                "gross_profit": gross,
                "operating_income": op,
                "net_income": ni,
                "eps_diluted": eps,
                "shares_diluted": shares_raw,
                "interest_expense": interest,
                "operating_cash_flow": ocf,
                "capex": capex,
                "dividends_paid": dividends,
                "buybacks": buybacks,
                "stock_based_comp": sbc,
                "total_assets": total_assets,
                "total_equity": equity,
                "total_debt": debt,
                "cash_and_equivalents": cash,
                "current_assets": current_assets,
                "current_liabilities": current_liabilities,
                "receivables": receivables,
                "inventory": inventory,
                "goodwill_intangibles": total_assets * 0.15,
                "debt_due_within_1y": debt * (0.45 if spec.archetype == "value_trap" else 0.12),
                "largest_customer_pct": spec.largest_customer_pct,
                "adjusted_profit_exclusions": adj_excl,
                "auditor": "Hartwell & Mane LLP",
                "sector_metrics": sector_metrics,
            }
            published = _dt(pe + timedelta(days=spec.publication_lag_days), hour=12)
            if published.date() > WORLD_NOW:
                self.quarter_data.append(
                    {"period_end": pe, "fields": fields, "published": None, "eps": eps, "revenue": rev_q}
                )
                continue
            reports.append(
                p.FundamentalPayload(
                    ticker=spec.ticker,
                    period_end=pe,
                    period_type="Q",
                    published_at=published,
                    currency=spec.currency,
                    fields=fields,
                    source_reference=f"synthetic://filings/{spec.ticker}/Q{pe.isoformat()}",
                    shares_outstanding=shares_raw,
                )
            )
            self.quarter_data.append(
                {"period_end": pe, "fields": fields, "published": published, "eps": eps, "revenue": rev_q}
            )

        # Restatement: revenue/income for one period restated down later.
        if spec.restated:
            pe_iso, pub_iso, haircut = spec.restated
            pe_r = date.fromisoformat(pe_iso)
            orig = next((q for q in self.quarter_data if q["period_end"] == pe_r), None)
            if orig is not None:
                fields = dict(orig["fields"])
                fields["revenue"] = fields["revenue"] * (1 - haircut)
                fields["net_income"] = fields["net_income"] * (1 - 2.2 * haircut)
                fields["eps_diluted"] = fields["net_income"] / fields["shares_diluted"]
                fields["receivables"] = fields["receivables"] * (1 - 1.5 * haircut)
                fields["auditor"] = "Calder Finch LLP"  # auditor change red flag
                reports.append(
                    p.FundamentalPayload(
                        ticker=spec.ticker,
                        period_end=pe_r,
                        period_type="Q",
                        published_at=_dt(date.fromisoformat(pub_iso), hour=13),
                        currency=spec.currency,
                        fields=fields,
                        is_restatement=True,
                        restates_period_end=pe_r,
                        source_reference=f"synthetic://filings/{spec.ticker}/restatement-{pe_iso}",
                    )
                )
        self.fundamentals = reports

    # -- estimates & targets --------------------------------------------

    def _fy_actual_eps(self, year: int) -> float | None:
        qs = [q for q in self.quarter_data if q["period_end"].year == year]
        if len(qs) < 4:
            return None
        return sum(q["eps"] for q in qs)

    def _build_estimates_targets(self) -> None:
        spec = self.spec
        rng = _rng("est", spec.ticker)
        month_ends = pd.bdate_range(date(2021, 1, 1), WORLD_NOW, freq="BME")
        estimates: list[p.EstimatePayload] = []
        targets: list[p.TargetPayload] = []
        eps_history: dict[str, list[tuple[date, float]]] = {}
        for ts in month_ends:
            snap_date = ts.date()
            if self.delist_ts is not None and ts > self.delist_ts:
                break
            for fy_offset in (0, 1):
                fy = snap_date.year + fy_offset
                fy_end = date(fy, 12, 31)
                if fy_end < snap_date:
                    continue
                actual = self._fy_actual_eps(fy)
                if actual is None:
                    # Extrapolate the last known run-rate for future years.
                    known = [q["eps"] for q in self.quarter_data if q["published"]]
                    if len(known) < 4:
                        continue
                    actual = sum(known[-4:]) * (1 + spec.revenue_growth_yoy[-1]) ** fy_offset
                days_out = max(0, (fy_end - snap_date).days)
                # Early estimates start biased against the eventual trend and
                # converge: positive estimate_trend => early consensus too low.
                bias0 = -spec.estimate_trend * 0.25
                mean = actual * (1 + bias0 * days_out / 540) * (1 + rng.normal(0, 0.01))
                label = f"FY{fy}"
                hist = eps_history.setdefault(label, [])
                mean_30 = next((v for d, v in reversed(hist) if (snap_date - d).days >= 28), None)
                mean_90 = next((v for d, v in reversed(hist) if (snap_date - d).days >= 85), None)
                up = down = 0
                if mean_30 is not None and abs(mean) > 1e-9:
                    delta = (mean - mean_30) / abs(mean_30) if mean_30 else 0.0
                    moved = min(spec.analyst_count, int(abs(delta) * 120))
                    up, down = (moved, 0) if delta > 0 else (0, moved)
                estimates.append(
                    p.EstimatePayload(
                        ticker=spec.ticker, as_of=snap_date, metric="eps",
                        fiscal_label=label, period_end=fy_end, mean=round(mean, 4),
                        high=round(mean * (1 + spec.target_dispersion), 4),
                        low=round(mean * (1 - spec.target_dispersion), 4),
                        analyst_count=spec.analyst_count,
                        mean_30d_ago=round(mean_30, 4) if mean_30 is not None else None,
                        mean_90d_ago=round(mean_90, 4) if mean_90 is not None else None,
                        up_revisions_30d=up, down_revisions_30d=down,
                    )
                )
                hist.append((snap_date, mean))
                # Revenue estimates ride the same machinery, scaled.
                rev_actual = sum(
                    q["revenue"] for q in self.quarter_data if q["period_end"].year == fy
                ) or None
                if rev_actual:
                    rev_mean = rev_actual * (1 + bias0 * days_out / 720)
                    estimates.append(
                        p.EstimatePayload(
                            ticker=spec.ticker, as_of=snap_date, metric="revenue",
                            fiscal_label=label, period_end=fy_end, mean=round(rev_mean, 0),
                            analyst_count=spec.analyst_count,
                        )
                    )
            price = self.econ_price_on(snap_date)
            raw_scale = 1.0
            if self.split_ts is not None and ts < self.split_ts:
                raw_scale = self.spec.split[1]  # type: ignore[index]
            t_mean = price * raw_scale * (1 + spec.target_bias) * (1 + rng.normal(0, 0.02))
            targets.append(
                p.TargetPayload(
                    ticker=spec.ticker, as_of=snap_date, currency=spec.currency,
                    mean=round(t_mean, 2),
                    high=round(t_mean * (1 + 1.6 * spec.target_dispersion), 2),
                    low=round(t_mean * (1 - 1.4 * spec.target_dispersion), 2),
                    std=round(t_mean * spec.target_dispersion, 2),
                    analyst_count=spec.analyst_count,
                    median_age_days=float(rng.integers(15, 75)),
                    mean_30d_ago=targets[-1].mean if targets else None,
                )
            )
        self.estimates = estimates
        self.targets = targets

    # -- news, catalysts, actions ----------------------------------------

    def _build_events(self) -> None:
        spec = self.spec
        rng = _rng("news", spec.ticker)
        news: list[p.NewsPayload] = []
        catalysts: list[p.CatalystPayload] = []
        actions: list[p.ActionPayload] = []

        surprise_by_archetype = {
            "breakout": 0.06, "inflection": 0.07, "compounder": 0.02,
            "deteriorating": -0.07, "value_trap": -0.05, "restatement": -0.02,
            "parabolic": 0.04, "oversold_quality": 0.015, "deep_value": 0.01,
        }
        base_surprise = surprise_by_archetype.get(spec.archetype, 0.0)

        published_quarters = [q for q in self.quarter_data if q["published"]]
        for q in published_quarters:
            pub: datetime = q["published"]
            pe: date = q["period_end"]
            surprise = base_surprise + float(rng.normal(0, 0.015))
            eps = q["eps"]
            rev = q["revenue"]
            qlabel = f"Q{((pe.month - 1) // 3) + 1} {pe.year}"
            news.append(
                p.NewsPayload(
                    ticker=spec.ticker,
                    external_id=f"earn-{pe.isoformat()}",
                    published_at=pub,
                    headline=(
                        f"{spec.name} reports {qlabel} results: EPS "
                        f"{'beats' if surprise >= 0 else 'misses'} consensus by "
                        f"{abs(surprise) * 100:.1f}%"
                    ),
                    summary=(
                        f"Revenue {rev / 1e6:,.0f}m {spec.currency}, diluted EPS "
                        f"{eps:.2f} {spec.currency}."
                    ),
                    source_name="RNS/PR Wire",
                    source_type="factual_event",
                    url=f"synthetic://news/{spec.ticker}/earn-{pe.isoformat()}",
                    sentiment=float(np.clip(surprise * 9, -0.9, 0.9)),
                    novelty=1.0,
                )
            )
            catalysts.append(
                p.CatalystPayload(
                    ticker=spec.ticker,
                    external_id=f"cat-earn-{pe.isoformat()}",
                    kind="earnings",
                    expected_date=pub.date(),
                    date_confirmed=True,
                    description=f"{qlabel} results",
                    binary=False,
                    published_at=_dt(pe - timedelta(days=30)),
                    resolved=True,
                    outcome=f"EPS surprise {surprise * 100:+.1f}%",
                    outcome_date=pub.date(),
                )
            )
        # Next earnings (upcoming).
        if published_quarters and (self.delist_ts is None):
            last_pub = published_quarters[-1]["published"].date()
            next_date = last_pub + timedelta(days=91)
            catalysts.append(
                p.CatalystPayload(
                    ticker=spec.ticker,
                    external_id="cat-earn-next",
                    kind="earnings",
                    expected_date=next_date,
                    date_confirmed=(next_date - WORLD_NOW).days <= 45,
                    description="Next quarterly results",
                    binary=spec.archetype in ("parabolic", "deteriorating"),
                    published_at=_dt(last_pub),
                )
            )

        for iso, direction, text in spec.guidance_events:
            d = date.fromisoformat(iso)
            sent = 0.7 if direction == "up" else -0.75
            news.append(
                p.NewsPayload(
                    ticker=spec.ticker, external_id=f"guid-{iso}", published_at=_dt(d, 8),
                    headline=f"{spec.name}: {text}", summary=text,
                    source_name="RNS/PR Wire", source_type="factual_event",
                    url=f"synthetic://news/{spec.ticker}/guid-{iso}",
                    sentiment=sent, novelty=1.0,
                )
            )
            news.append(
                p.NewsPayload(
                    ticker=spec.ticker, external_id=f"guid-an-{iso}",
                    published_at=_dt(d + timedelta(days=1), 9),
                    headline=(
                        f"Analysts {'raise' if direction == 'up' else 'cut'} "
                        f"estimates for {spec.name} after guidance change"
                    ),
                    summary="Sell-side reaction to updated guidance.",
                    source_name="Broker notes", source_type="analyst_opinion",
                    sentiment=sent * 0.7, novelty=0.5,
                )
            )
            catalysts.append(
                p.CatalystPayload(
                    ticker=spec.ticker, external_id=f"cat-guid-{iso}", kind="guidance",
                    expected_date=d, date_confirmed=True, description=text,
                    published_at=_dt(d, 8), resolved=True,
                    outcome=f"Guidance {direction}", outcome_date=d,
                )
            )

        for kind, iso, desc, binary in spec.special_catalysts:
            d = date.fromisoformat(iso)
            announced = _dt(max(HISTORY_START, d - timedelta(days=45)), 9)
            resolved = (d <= WORLD_NOW and kind not in ("refinancing", "regulatory")) or (
                d <= WORLD_NOW and not binary
            )
            catalysts.append(
                p.CatalystPayload(
                    ticker=spec.ticker, external_id=f"cat-{kind}-{iso}", kind=kind,
                    expected_date=d, date_confirmed=(d - WORLD_NOW).days <= 60,
                    description=desc, binary=binary, published_at=announced,
                    resolved=bool(resolved and d <= WORLD_NOW),
                    outcome=desc if (resolved and d <= WORLD_NOW) else None,
                    outcome_date=d if (resolved and d <= WORLD_NOW) else None,
                )
            )
            if d <= WORLD_NOW:
                news.append(
                    p.NewsPayload(
                        ticker=spec.ticker, external_id=f"news-{kind}-{iso}",
                        published_at=_dt(d, 8), headline=f"{spec.name}: {desc}",
                        summary=desc, source_name="RNS/PR Wire",
                        source_type="factual_event",
                        url=f"synthetic://news/{spec.ticker}/{kind}-{iso}",
                        sentiment=0.5 if kind in ("contract", "capital_return") else -0.4
                        if kind in ("regulatory", "refinancing") else 0.1,
                        novelty=1.0,
                    )
                )

        # Management always talks its book after bad guidance (disagreement).
        if spec.archetype in ("deteriorating", "value_trap"):
            for iso, _direction, _text in spec.guidance_events or ():
                d = date.fromisoformat(iso)
                news.append(
                    p.NewsPayload(
                        ticker=spec.ticker, external_id=f"mgmt-{iso}",
                        published_at=_dt(d + timedelta(days=2), 10),
                        headline=f"{spec.name} CEO: 'the underlying franchise remains strong'",
                        summary="Management interview following the guidance revision.",
                        source_name="Business TV", source_type="management_claim",
                        sentiment=0.4, novelty=0.4,
                    )
                )

        if spec.social_hype:
            hype_days = pd.bdate_range(
                BDAYS[int(len(BDAYS) * 0.86)], WORLD_NOW, freq="W-WED"
            )
            for i, ts in enumerate(hype_days):
                news.append(
                    p.NewsPayload(
                        ticker=spec.ticker, external_id=f"social-{i}",
                        published_at=_dt(ts.date(), 16),
                        headline=f"${spec.ticker} trending on retail boards — 'this is the next big thing'",
                        summary="High-volume social chatter, mostly repetitive.",
                        source_name="Social aggregate", source_type="social",
                        sentiment=0.85, novelty=0.15,
                    )
                )

        # Corporate actions: dividends, split, acquisition/delisting.
        if spec.dividend_yield > 0:
            for q in self.quarter_data:
                ex = q["period_end"] + timedelta(days=20)
                if ex > WORLD_NOW or (self.delist_ts and pd.Timestamp(ex) > self.delist_ts):
                    continue
                price = self.econ_price_on(ex)
                raw_scale = 1.0
                if self.split_ts is not None and pd.Timestamp(ex) < self.split_ts:
                    raw_scale = self.spec.split[1]  # type: ignore[index]
                amount = price * raw_scale * spec.dividend_yield / 4
                actions.append(
                    p.ActionPayload(
                        ticker=spec.ticker, kind="dividend", ex_date=ex,
                        amount=round(amount, 4),
                        detail=f"Quarterly dividend {round(amount, 4)} {spec.currency}",
                        published_at=_dt(ex - timedelta(days=25)),
                    )
                )
        if spec.split:
            ex_iso, factor = spec.split
            actions.append(
                p.ActionPayload(
                    ticker=spec.ticker, kind="split", ex_date=date.fromisoformat(ex_iso),
                    factor=factor, detail=f"{int(factor)}:1 share split",
                    published_at=_dt(date.fromisoformat(ex_iso) - timedelta(days=30)),
                )
            )
        if spec.acquired:
            ann, delist, premium = spec.acquired
            ann_d, delist_d = date.fromisoformat(ann), date.fromisoformat(delist)
            actions.append(
                p.ActionPayload(
                    ticker=spec.ticker, kind="acquisition", ex_date=ann_d,
                    detail=f"Agreed cash offer at {premium * 100:.0f}% premium",
                    published_at=_dt(ann_d, 8),
                )
            )
            actions.append(
                p.ActionPayload(
                    ticker=spec.ticker, kind="delisting", ex_date=delist_d,
                    detail="Delisted on completion of acquisition",
                    published_at=_dt(delist_d, 8),
                )
            )
            news.append(
                p.NewsPayload(
                    ticker=spec.ticker, external_id="acq-news",
                    published_at=_dt(ann_d, 8),
                    headline=f"{spec.name} agrees cash takeover at {premium * 100:.0f}% premium",
                    summary="Recommended cash acquisition.", source_name="RNS/PR Wire",
                    source_type="factual_event", sentiment=0.8, novelty=1.0,
                )
            )
            catalysts.append(
                p.CatalystPayload(
                    ticker=spec.ticker, external_id="cat-acq", kind="m_and_a",
                    expected_date=delist_d, date_confirmed=True,
                    description="Cash acquisition completion", binary=False,
                    published_at=_dt(ann_d, 8), resolved=delist_d <= WORLD_NOW,
                    outcome="Completed", outcome_date=delist_d,
                )
            )
        self.news = news
        self.catalysts = catalysts
        self.actions = actions

    # -- ownership -------------------------------------------------------

    def _build_ownership(self) -> None:
        spec = self.spec
        rng = _rng("own", spec.ticker)
        shorts: list[p.ShortInterestPayload] = []
        fridays = pd.bdate_range(HISTORY_START, WORLD_NOW, freq="2W-FRI")
        level = spec.short_pct_float
        for ts in fridays:
            if self.delist_ts is not None and ts > self.delist_ts:
                break
            level = max(0.3, level + float(rng.normal(0, 0.25)))
            boost = 1.0
            if spec.archetype == "parabolic" and ts > BDAYS[int(len(BDAYS) * 0.85)]:
                boost = 1.6
            shares_short = self.shares_raw_on(ts.date()) * level * boost / 100
            adv = float(self.bars["volume"].loc[self.bars.index <= ts].iloc[-20:].mean())
            shorts.append(
                p.ShortInterestPayload(
                    ticker=spec.ticker, as_of=ts.date(),
                    published_at=_dt(ts.date() + timedelta(days=9)),
                    shares_short=shares_short,
                    pct_float=round(level * boost, 2),
                    days_to_cover=round(shares_short / adv, 1) if adv > 0 else None,
                )
            )
        self.short_interest = shorts

        insiders: list[p.InsiderPayload] = []
        if spec.insider_pattern == "buys_at_lows":
            closes = self.bars["close"]
            for ts in pd.bdate_range(HISTORY_START + timedelta(days=400), WORLD_NOW, freq="BQE"):
                window = closes.loc[closes.index <= ts].iloc[-252:]
                if window.empty:
                    continue
                px = float(window.iloc[-1])
                if px <= float(window.quantile(0.12)):
                    for role, mult in (("CEO", 3.0), ("CFO", 1.5), ("Director", 1.0)):
                        insiders.append(
                            p.InsiderPayload(
                                ticker=spec.ticker,
                                filed_at=_dt(ts.date() + timedelta(days=2), 18),
                                transaction_date=ts.date(),
                                insider_name=f"{role} of {spec.ticker}",
                                insider_role=role, kind="buy",
                                shares=round(40000 * mult), value=round(40000 * mult * px, 0),
                            )
                        )
        elif spec.insider_pattern == "selling":
            closes = self.bars["close"]
            for i, ts in enumerate(
                pd.bdate_range(BDAYS[int(len(BDAYS) * 0.86)], WORLD_NOW, freq="4W-THU")
            ):
                px_ser = closes.loc[closes.index <= ts]
                if px_ser.empty:
                    continue
                px = float(px_ser.iloc[-1])
                insiders.append(
                    p.InsiderPayload(
                        ticker=spec.ticker, filed_at=_dt(ts.date() + timedelta(days=2), 18),
                        transaction_date=ts.date(),
                        insider_name="Founder", insider_role="CEO", kind="sell",
                        shares=250000 + 50000 * i, value=round((250000 + 50000 * i) * px, 0),
                    )
                )
        self.insiders = insiders


# ---------------------------------------------------------------------------
# Macro & FX
# ---------------------------------------------------------------------------


def build_macro() -> list[p.MacroPayload]:
    out: list[p.MacroPayload] = []
    months = pd.date_range(HISTORY_START, WORLD_NOW, freq="ME")

    def rate_path(peaks: dict[int, float]) -> list[float]:
        vals = []
        for ts in months:
            yi = min(max(ts.year - 2020, 0), 6)
            vals.append(peaks[yi])
        return vals

    us_rate = rate_path({0: 0.25, 1: 0.25, 2: 3.0, 3: 5.25, 4: 5.0, 5: 4.25, 6: 3.75})
    uk_rate = rate_path({0: 0.10, 1: 0.25, 2: 2.25, 3: 5.0, 4: 4.75, 5: 4.0, 6: 3.5})
    us_cpi = rate_path({0: 1.4, 1: 4.5, 2: 8.5, 3: 4.0, 4: 2.9, 5: 2.5, 6: 2.4})
    uk_cpi = rate_path({0: 0.8, 1: 3.8, 2: 9.5, 3: 6.0, 4: 2.8, 5: 2.6, 6: 2.5})
    for series, vals, lag in (
        ("us_policy_rate", us_rate, 0),
        ("uk_policy_rate", uk_rate, 0),
        ("us_cpi_yoy", us_cpi, 12),
        ("uk_cpi_yoy", uk_cpi, 14),
        ("us_10y_yield", [r + 0.6 for r in us_rate], 0),
        ("uk_10y_yield", [r + 0.5 for r in uk_rate], 0),
    ):
        for ts, v in zip(months, vals, strict=True):
            obs = ts.date()
            pub = _dt(obs + timedelta(days=lag), 13)
            if pub.date() > WORLD_NOW:
                continue
            out.append(p.MacroPayload(series_id=series, obs_date=obs, value=float(v), published_at=pub))

    # VIX-like and credit spreads derive from the US market path (weekly).
    mkt = market_returns("US")
    weekly = pd.bdate_range(HISTORY_START + timedelta(days=30), WORLD_NOW, freq="W-FRI")
    rolling = mkt.rolling(21).std()
    for ts in weekly:
        upto = rolling.loc[rolling.index <= ts]
        if upto.empty or pd.isna(upto.iloc[-1]):
            continue
        vix = 12 + float(upto.iloc[-1]) * 1400
        out.append(
            p.MacroPayload(series_id="vix", obs_date=ts.date(), value=round(vix, 2), published_at=_dt(ts.date()))
        )
        spread = 90 + float(upto.iloc[-1]) * 9000
        out.append(
            p.MacroPayload(
                series_id="us_credit_spread_bps", obs_date=ts.date(),
                value=round(spread, 1), published_at=_dt(ts.date()),
            )
        )
    return out


def build_fx() -> list[p.FxPayload]:
    rng = _rng("fx", "USDGBP")
    rate = 0.80
    out = []
    for ts in BDAYS:
        rate += rng.normal(0, 0.002) + (0.79 - rate) * 0.01  # mean-reverting
        rate = float(np.clip(rate, 0.70, 0.88))
        out.append(
            p.FxPayload(base_ccy="USD", quote_ccy="GBP", rate_date=ts.date(), rate=round(rate, 5))
        )
    return out
