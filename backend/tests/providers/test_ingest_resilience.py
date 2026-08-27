"""Real-data configurations legitimately leave capabilities unconfigured —
ingest must record that in Data Health and continue, never crash."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from vigil.config import reset_settings_cache
from vigil.models import Instrument, PriceBar, ProviderHealthRecord
from vigil.providers.registry import reset_provider_cache


def test_unconfigured_capabilities_do_not_sink_ingest(tmp_path, monkeypatch):
    universe = tmp_path / "u.yml"
    universe.write_text(
        "instruments:\n"
        "  - {ticker: '^SPX', name: 'S&P 500', market: US, sector: '', security_type: index}\n"
        "  - {ticker: NVLT, name: Novalight, market: US, sector: Technology}\n"
    )
    monkeypatch.setenv("VIGIL_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("VIGIL_PROVIDER_REFERENCE", "static")
    monkeypatch.setenv("VIGIL_UNIVERSE_FILE", str(universe))
    # Synthetic prices know NVLT; everything else is switched OFF.
    monkeypatch.setenv("VIGIL_PROVIDER_PRICE", "synthetic")
    monkeypatch.setenv("VIGIL_PROVIDER_FUNDAMENTALS", "")
    monkeypatch.setenv("VIGIL_PROVIDER_ESTIMATES", "")
    monkeypatch.setenv("VIGIL_PROVIDER_NEWS", "")
    monkeypatch.setenv("VIGIL_PROVIDER_MACRO", "")
    reset_settings_cache()
    reset_provider_cache()
    import vigil.db as db

    db.dispose_engine()
    try:
        db.create_all()
        from vigil.jobs.ingest_all import ingest_universe

        with db.session_scope() as session:
            ingest_universe(session, date(2026, 6, 1), date(2026, 8, 25))
            instruments = {
                i.ticker for i in session.execute(select(Instrument)).scalars()
            }
            assert instruments == {"^SPX", "NVLT"}
            bar_count = session.execute(select(PriceBar)).scalars().all()
            assert len(bar_count) > 0  # NVLT bars ingested from synthetic prices
            health = list(session.execute(select(ProviderHealthRecord)).scalars())
            unconfigured = {
                h.capability for h in health if not h.configured
            }
            assert {"fundamentals", "estimates", "news", "macro"} <= unconfigured
    finally:
        db.dispose_engine()
        reset_settings_cache()
        reset_provider_cache()
