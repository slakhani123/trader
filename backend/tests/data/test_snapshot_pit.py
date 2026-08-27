"""Point-in-time discipline tests for the snapshot builder.

These are the guarantees the whole platform rests on: no look-ahead, no
survivorship bias, no premature restatements, corporate-action-correct
prices as they were seen at the time.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from vigil.data.snapshot import build_snapshot
from vigil.models import Instrument


def _iid(session, ticker: str) -> int:
    return session.execute(
        select(Instrument.id).where(Instrument.ticker == ticker)
    ).scalar_one()


class TestPricePIT:
    def test_no_bars_after_as_of(self, seeded_session):
        snap = build_snapshot(seeded_session, _iid(seeded_session, "NVLT"), date(2024, 3, 15),
                              include_peers=False)
        assert snap.prices.index.max().date() <= date(2024, 3, 15)
        assert snap.benchmark.index.max().date() <= date(2024, 3, 15)

    def test_split_not_applied_before_ex_date(self, seeded_session):
        """PYNE splits 4:1 on 2024-06-10. A snapshot taken before must see
        raw == adjusted; after, earlier bars are divided by 4."""
        iid = _iid(seeded_session, "PYNE")
        before = build_snapshot(seeded_session, iid, date(2024, 5, 1), include_peers=False)
        raw = float(before.prices["close"].iloc[-1])
        adj = float(before.prices["adj_close"].iloc[-1])
        assert raw == pytest.approx(adj, rel=0.02)  # only dividends adjust, none here

        after = build_snapshot(seeded_session, iid, date(2024, 7, 1), include_peers=False)
        same_bar = after.prices.loc[before.prices.index[-1]]
        assert float(same_bar["adj_close"]) == pytest.approx(raw / 4.0, rel=1e-6)
        assert float(same_bar["close"]) == pytest.approx(raw)  # raw stays raw

    def test_delisted_instrument_keeps_history_no_future_bars(self, seeded_session):
        iid = _iid(seeded_session, "TLLM")
        snap = build_snapshot(seeded_session, iid, date(2026, 8, 25), include_peers=False)
        assert snap.info.delisted_at == date(2025, 5, 30)
        assert not snap.info.is_active
        assert snap.prices.index.max().date() <= date(2025, 5, 30)
        # And before the acquisition announcement it looked perfectly alive.
        earlier = build_snapshot(seeded_session, iid, date(2025, 1, 15), include_peers=False)
        assert earlier.info.is_active
        assert earlier.info.delisted_at is None


class TestFundamentalsPIT:
    def test_report_invisible_before_publication(self, seeded_session):
        """NVLT's Q2-2026 (period end 30 Jun) publishes ~14 Aug. On 1 Aug the
        snapshot must not contain it."""
        iid = _iid(seeded_session, "NVLT")
        aug1 = build_snapshot(seeded_session, iid, date(2026, 8, 1), include_peers=False)
        assert all(f.period_end != date(2026, 6, 30) for f in aug1.fundamentals)
        aug25 = build_snapshot(seeded_session, iid, date(2026, 8, 25), include_peers=False)
        assert any(f.period_end == date(2026, 6, 30) for f in aug25.fundamentals)

    def test_restatement_only_visible_after_its_own_publication(self, seeded_session):
        """VNTA restates Q2-2025 revenue down ~8% on 2025-11-14."""
        iid = _iid(seeded_session, "VNTA")

        def q2_rev(as_of: date) -> float:
            snap = build_snapshot(seeded_session, iid, as_of, include_peers=False)
            recs = [q for q in snap.quarterlies() if q.period_end == date(2025, 6, 30)]
            assert recs, f"Q2-2025 missing at {as_of}"
            return recs[0].revenue

        original = q2_rev(date(2025, 11, 1))
        restated = q2_rev(date(2025, 12, 1))
        assert restated < original * 0.95
        # The restated view is the effective one from then on.
        assert q2_rev(date(2026, 8, 25)) == pytest.approx(restated)

    def test_publication_before_period_end_refused_at_ingest(self, session):
        from datetime import datetime

        from vigil.data import ingest
        from vigil.models import Instrument
        from vigil.providers import base as p

        inst = Instrument(
            ticker="X", exchange="T", market="US", name="X", sector="S",
            industry="I", currency="USD",
        )
        session.add(inst)
        session.flush()
        stats = ingest.ingest_fundamentals(
            session, inst,
            [p.FundamentalPayload(
                ticker="X", period_end=date(2026, 6, 30), period_type="Q",
                published_at=datetime(2026, 5, 1, 12), currency="USD",
                fields={"revenue": 1.0},
            )],
            "test",
        )
        assert stats.rejected == 1 and stats.inserted == 0


class TestEventPIT:
    def test_catalyst_resolution_masked_until_outcome_date(self, seeded_session):
        """MERI's FDA warning letter lands 2026-07-08. Before that date the
        catalyst may be announced but must not carry its outcome."""
        iid = _iid(seeded_session, "MERI")
        before = build_snapshot(seeded_session, iid, date(2026, 7, 1), include_peers=False)
        for c in before.catalysts:
            if c.expected_date == date(2026, 7, 8):
                assert not c.resolved and c.outcome is None
        after = build_snapshot(seeded_session, iid, date(2026, 7, 20), include_peers=False)
        resolved = [c for c in after.catalysts if c.expected_date == date(2026, 7, 8)]
        assert resolved and resolved[0].resolved

    def test_news_respects_publication_time(self, seeded_session):
        iid = _iid(seeded_session, "ARWD")
        snap = build_snapshot(seeded_session, iid, date(2026, 7, 15), include_peers=False)
        assert all(n.published_at.date() <= date(2026, 7, 15) for n in snap.news)
        # The 21 Jul contract-win headline must NOT be visible on the 15th.
        assert not any("contract" in n.headline.lower() for n in snap.news
                       if n.published_at.date() > date(2026, 7, 15))


class TestQualityFlags:
    def test_stale_prices_flagged(self, seeded_session):
        iid = _iid(seeded_session, "TLLM")  # delisted → very stale by 2026
        snap = build_snapshot(seeded_session, iid, date(2026, 8, 25), include_peers=False)
        assert snap.liquidity.price_staleness_days > 200
        assert any("stale" in w for w in snap.quality.warnings)

    def test_microcap_liquidity_measured(self, seeded_session):
        iid = _iid(seeded_session, "MICR")
        snap = build_snapshot(seeded_session, iid, date(2026, 8, 25), include_peers=False)
        assert snap.liquidity.median_daily_traded_value_base is not None
        assert snap.liquidity.median_daily_traded_value_base < 250_000

    def test_fx_applied_for_us_names(self, seeded_session):
        iid = _iid(seeded_session, "NVLT")
        snap = build_snapshot(seeded_session, iid, date(2026, 8, 25), include_peers=False)
        assert snap.info.currency == "USD"
        assert 0.70 <= snap.fx_to_base <= 0.88
        assert snap.fx_as_of is not None


class TestBenchmarkResolution:
    def test_duplicate_index_rows_pick_the_one_with_data(self, session):
        """Editing universe.yml adds instruments but never deletes old ones:
        swapping ^SPX for SPY leaves two US index rows, one with no bars.
        The benchmark lookup must not crash and must use the live one."""
        from vigil.data.snapshot import _index_series
        from vigil.models import PriceBar

        stale = Instrument(
            ticker="^SPX", exchange="INDEX", market="US", name="S&P 500",
            sector="", industry="", currency="USD", security_type="index",
        )
        live = Instrument(
            ticker="SPY", exchange="NYSE", market="US", name="S&P 500 ETF",
            sector="", industry="", currency="USD", security_type="index",
        )
        session.add_all([stale, live])
        session.flush()
        for i in range(5):
            session.add(PriceBar(
                instrument_id=live.id, bar_date=date(2026, 8, 17 + i),
                open=640.0, high=645.0, low=638.0, close=642.0 + i,
                volume=1e6, currency="USD",
            ))
        session.flush()

        series = _index_series(session, "US", "", date(2026, 8, 25))
        assert series is not None and series.name == "SPY" and len(series) == 5
        assert _index_series(session, "UK", "", date(2026, 8, 25)) is None
