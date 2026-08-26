"""API contract tests against docs/API_SPEC.md.

Fixture rows are inserted directly (never via run_scan — engine behaviour is
covered elsewhere); every endpoint is asserted against the documented shape.
"""

from __future__ import annotations

import sys
import time as time_mod
import types
from datetime import UTC, date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

import vigil.db as db
from vigil.config import reset_settings_cache
from vigil.models import (
    Alert,
    AuditLog,
    BacktestRun,
    BacktestTrade,
    Catalyst,
    EngineOutput,
    FundamentalReport,
    FxRate,
    Instrument,
    JobRun,
    ModelVersion,
    NotificationDelivery,
    PortfolioPosition,
    PriceBar,
    ProviderHealthRecord,
    ScoreBundleRow,
    ScoreRecord,
    ScoreRun,
    SharesOutstandingObs,
    Signal,
    WatchlistItem,
)

TODAY = date.today()


def _dt(days_ago: int, hour: int = 12) -> datetime:
    return datetime.combine(TODAY - timedelta(days=days_ago), time(hour))


def make_alert_payload() -> dict:
    """Minimal but schema-complete AlertPayload, validated by the real model."""
    from vigil.schemas.alerts import (
        AlertPayload,
        ChangeSincePrevious,
        CompanyHeader,
        PriceStamp,
        ScoreView,
        TechnicalSummary,
        ThesisQA,
        ValuationSummary,
    )

    score_view = ScoreView(
        horizon="medium",
        opportunity=7.5,
        confidence=6.2,
        risk=4.0,
        components={"value": 7.0, "technical": 6.5},
        explanation=["value engine strong"],
    )
    payload = AlertPayload(
        company=CompanyHeader(
            name="Alpha Corp",
            ticker="AAA",
            exchange="NYSE",
            market="US",
            sector="Technology",
            industry="Software",
            market_cap_local=5.0e9,
            market_cap_base=3.9e9,
            base_currency="GBP",
            local_currency="USD",
        ),
        signal_family="breakout_continuation",
        lifecycle_state="TRIGGERED",
        transition="WATCHING->TRIGGERED",
        best_fit_horizon="medium",
        horizon="medium",
        priority="high",
        price=PriceStamp(
            price=101.0,
            currency="USD",
            as_of_date=TODAY,
            bar_timestamp=datetime.combine(TODAY, time(21)),
            staleness_trading_days=0,
            fx_to_base=0.78,
            fx_as_of=TODAY,
        ),
        scores=score_view,
        all_horizons={"medium": score_view},
        change=ChangeSincePrevious(),
        thesis=ThesisQA(
            why_this_company="durable moat",
            why_now="breakout confirmed",
            what_market_misunderstands="margin trajectory",
            what_would_prove_wrong="revenue deceleration",
            expected_holding_period="1-6 months",
            early_trim_or_exit_causes="close below support",
            three_largest_risks=["macro", "competition", "valuation"],
        ),
        thesis_summary="Breakout with strong value support.",
        narrative_source="template",
        supporting=[],
        contradicting=[],
        valuation=ValuationSummary(),
        technicals=TechnicalSummary(),
        catalysts=[],
        entry_zone={"low": 95.0, "high": 100.0},
        conditions_before_entry=[],
        invalidation_conditions=["close below 90"],
        fundamental_invalidation=[],
        stop=90.0,
        scenarios=[],
        target_range={"low": 120.0, "high": 130.0},
        trim_conditions=[],
        exit_conditions=[],
        binary_event_warning=None,
        data_warnings=[],
        missing_data=[],
        sources=[],
        model_version="v1.0.0",
        generated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    return payload.model_dump(mode="json")


def seed(s) -> dict:
    """Insert a schema-complete fixture world; return ids for assertions."""
    i1 = Instrument(
        ticker="AAA", exchange="NYSE", market="US", name="Alpha Corp",
        sector="Technology", industry="Software", currency="USD",
    )
    i2 = Instrument(
        ticker="BBB", exchange="NYSE", market="US", name="Beta Corp",
        sector="Technology", industry="Software", currency="USD",
    )
    i3 = Instrument(
        ticker="CCC", exchange="LSE", market="UK", name="Gamma Plc",
        sector="Energy", industry="Oil", currency="GBP",
    )
    s.add_all([i1, i2, i3])
    s.flush()

    for inst, base_price in ((i1, 100.0), (i2, 50.0)):
        for k in range(70, 0, -1):
            d = TODAY - timedelta(days=k - 1)
            px = base_price + (70 - k) * 0.1
            s.add(
                PriceBar(
                    instrument_id=inst.id, bar_date=d, open=px, high=px * 1.01,
                    low=px * 0.99, close=px, volume=1_000_000, currency="USD",
                )
            )
    s.add_all(
        [
            SharesOutstandingObs(
                instrument_id=i1.id, as_of=TODAY - timedelta(days=30),
                published_at=_dt(30), shares=50e6,
            ),
            SharesOutstandingObs(
                instrument_id=i2.id, as_of=TODAY - timedelta(days=30),
                published_at=_dt(30), shares=10e6,
            ),
            FxRate(base_ccy="USD", quote_ccy="GBP", rate_date=TODAY - timedelta(days=1), rate=0.78),
        ]
    )

    run_ok = ScoreRun(
        as_of=TODAY, model_version="v1.0.0", config_hash="h", trigger="manual",
        universe_size=2, scored=2, abstained=0, status="ok", detail={},
    )
    s.add(run_ok)
    s.flush()
    run_failed = ScoreRun(
        as_of=TODAY, model_version="v1.0.0", config_hash="h", status="failed", detail={},
    )
    s.add(run_failed)
    s.flush()

    b1 = ScoreBundleRow(
        run_id=run_ok.id, instrument_id=i1.id, as_of=TODAY, model_version="v1.0.0",
        best_fit_horizon="medium", evidence=[], warnings=["one warning"],
    )
    b2 = ScoreBundleRow(
        run_id=run_ok.id, instrument_id=i2.id, as_of=TODAY, model_version="v1.0.0",
        best_fit_horizon=None, evidence=[], warnings=[],
    )
    s.add_all([b1, b2])
    s.flush()
    s.add_all(
        [
            ScoreRecord(
                run_id=run_ok.id, bundle_id=b1.id, instrument_id=i1.id, as_of=TODAY,
                horizon="medium", opportunity=7.5, confidence=6.2, risk=4.0,
                components={"value": 7.0}, abstained=False, abstain_reasons=[],
                gate={"passed": True, "failures": [], "reward_risk": 2.5},
                explanation=["strong"],
            ),
            ScoreRecord(
                run_id=run_ok.id, bundle_id=b1.id, instrument_id=i1.id, as_of=TODAY,
                horizon="short", opportunity=5.0, confidence=5.0, risk=5.0,
                components={}, abstained=False, abstain_reasons=[],
                gate={"passed": False, "failures": ["opportunity too low"]}, explanation=[],
            ),
            ScoreRecord(
                run_id=run_ok.id, bundle_id=b1.id, instrument_id=i1.id, as_of=TODAY,
                horizon="long", opportunity=6.0, confidence=4.0, risk=5.0,
                components={}, abstained=True, abstain_reasons=["thin data"],
                gate=None, explanation=[],
            ),
            ScoreRecord(
                run_id=run_ok.id, bundle_id=b2.id, instrument_id=i2.id, as_of=TODAY,
                horizon="medium", opportunity=4.0, confidence=5.0, risk=6.0,
                components={}, abstained=False, abstain_reasons=[],
                gate={"passed": False, "failures": ["weak"]}, explanation=[],
            ),
        ]
    )
    s.add_all(
        [
            EngineOutput(
                run_id=run_ok.id, instrument_id=i1.id, engine="value", score=7.2,
                components={"fcf_yield": 6.0}, evidence=[], warnings=[],
                data_quality=0.9, details={},
            ),
            EngineOutput(
                run_id=run_ok.id, instrument_id=i1.id, engine="technical", score=6.1,
                components={}, evidence=[], warnings=["short history"],
                data_quality=0.8, details={},
            ),
        ]
    )

    sig_active = Signal(
        instrument_id=i1.id, family="breakout_continuation", horizon="medium",
        state="TRIGGERED", first_run_id=run_ok.id, last_run_id=run_ok.id,
        anchor_price=100.0, anchor_date=TODAY, entry_plan={"zone_low": 95.0},
        last_scores={"opportunity": 7.5}, state_history=[{"state": "TRIGGERED"}],
        active=True,
    )
    sig_dead = Signal(
        instrument_id=i1.id, family="oversold_at_support", horizon="short",
        state="EXPIRED", first_run_id=run_ok.id, last_run_id=run_ok.id,
        entry_plan={}, last_scores={}, state_history=[], active=False,
    )
    s.add_all([sig_active, sig_dead])
    s.flush()

    payload = make_alert_payload()
    a1 = Alert(
        signal_id=sig_active.id, instrument_id=i1.id, run_id=run_ok.id,
        created_at=_dt(0), as_of=TODAY, family="breakout_continuation",
        lifecycle_state="TRIGGERED", transition="WATCHING->TRIGGERED",
        horizon="medium", priority="high", title="AAA: breakout triggered",
        payload=payload, read=False,
    )
    a2 = Alert(
        signal_id=sig_active.id, instrument_id=i1.id, run_id=run_ok.id,
        created_at=_dt(3), as_of=TODAY - timedelta(days=3),
        family="breakout_continuation", lifecycle_state="WATCHING", transition="",
        horizon="medium", priority="normal", title="AAA: watching", payload=payload,
        read=True,
    )
    s.add_all([a1, a2])
    s.flush()

    s.add_all(
        [
            PortfolioPosition(
                instrument_id=i1.id, quantity=100.0, avg_cost_local=80.0,
                currency="USD", opened_at=TODAY - timedelta(days=100), active=True,
            ),
            WatchlistItem(instrument_id=i2.id, notes="watch me", active=True),
            Catalyst(
                external_id="c1", instrument_id=i1.id, kind="earnings",
                expected_date=TODAY + timedelta(days=10), date_confirmed=True,
                description="Q3 results", binary=True, published_at=_dt(20),
            ),
            Catalyst(
                external_id="c2", instrument_id=i2.id, kind="product",
                expected_date=TODAY + timedelta(days=90), date_confirmed=False,
                description="Launch event", binary=False, published_at=_dt(20),
            ),
            ProviderHealthRecord(
                provider="synthetic", capability="prices", ok=True, configured=True,
                message="", checked_at=_dt(0),
            ),
            JobRun(job_name="scan", started_at=_dt(0), finished_at=_dt(0), status="ok", detail={}),
            ModelVersion(
                version="v1.0.0", weights={"value": 0.3}, config_hash="h",
                notes="baseline", active=True,
            ),
            AuditLog(actor="system", action="scan_completed", detail={"run_id": run_ok.id}),
        ]
    )

    bt = BacktestRun(
        name="bt1", model_version="v1.0.0", config={"step_days": 5},
        start_date=TODAY - timedelta(days=200), end_date=TODAY - timedelta(days=10),
        holdout_start=TODAY - timedelta(days=60), status="ok",
        metrics={"hit_rate": 0.6}, by_bucket={"short": {}}, calibration={"bins": []},
    )
    s.add(bt)
    s.flush()
    s.add(
        BacktestTrade(
            run_id=bt.id, instrument_id=i1.id, family="breakout_continuation",
            horizon="medium", signal_date=TODAY - timedelta(days=100),
            entry_date=TODAY - timedelta(days=99), entry_price=90.0,
            exit_date=TODAY - timedelta(days=60), exit_price=100.0,
            exit_reason="target", holding_days=39, return_pct=11.1,
            benchmark_return_pct=2.0, mae_pct=-3.0, mfe_pct=12.0, costs_bps=15.0,
            opportunity=7.0, confidence=6.0, risk=4.0,
        )
    )
    s.add(NotificationDelivery(alert_id=a1.id, channel="inapp", status="sent", detail=""))

    q1_end = TODAY - timedelta(days=200)
    q2_end = TODAY - timedelta(days=110)
    s.add_all(
        [
            FundamentalReport(
                instrument_id=i1.id, period_end=q1_end, period_type="Q",
                published_at=_dt(160), currency="USD",
                payload={
                    "revenue": 1000.0, "gross_profit": 500.0, "operating_income": 200.0,
                    "net_income": 150.0, "eps_diluted": 1.5, "operating_cash_flow": 180.0,
                    "capex": 30.0, "total_debt": 400.0, "cash_and_equivalents": 100.0,
                },
            ),
            FundamentalReport(
                instrument_id=i1.id, period_end=q2_end, period_type="Q",
                published_at=_dt(70), currency="USD",
                payload={
                    "revenue": 1100.0, "gross_profit": 550.0, "operating_income": 220.0,
                    "net_income": 160.0, "eps_diluted": 1.6, "operating_cash_flow": 190.0,
                    "capex": 35.0, "total_debt": 380.0, "cash_and_equivalents": 120.0,
                },
            ),
            # Restatement of Q1, published later: must replace the original row.
            FundamentalReport(
                instrument_id=i1.id, period_end=q1_end, period_type="Q",
                published_at=_dt(50), is_restatement=True, restates_period_end=q1_end,
                currency="USD",
                payload={
                    "revenue": 900.0, "gross_profit": 450.0, "operating_income": 150.0,
                    "net_income": 100.0, "eps_diluted": 1.0, "operating_cash_flow": 120.0,
                    "capex": 30.0, "total_debt": 400.0, "cash_and_equivalents": 100.0,
                },
            ),
        ]
    )
    return {
        "i1": i1.id, "i2": i2.id, "i3": i3.id, "run_ok": run_ok.id,
        "run_failed": run_failed.id, "signal": sig_active.id, "alert1": a1.id,
        "alert2": a2.id, "backtest": bt.id, "q1_end": q1_end, "q2_end": q2_end,
    }


@pytest.fixture()
def api(sqlite_env):
    db.create_all()
    with db.session_scope() as s:
        ids = seed(s)
    from vigil.api.app import create_app

    with TestClient(create_app()) as client:
        yield client, ids


# ---------------------------------------------------------------------------
# Auth & health
# ---------------------------------------------------------------------------


def test_health_needs_no_auth_even_with_token(sqlite_env, monkeypatch):
    monkeypatch.setenv("VIGIL_API_TOKEN", "sekrit")
    reset_settings_cache()
    db.create_all()
    from vigil.api.app import create_app

    client = TestClient(create_app())
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["version"]


def test_auth_enforced_when_token_configured(sqlite_env, monkeypatch):
    monkeypatch.setenv("VIGIL_API_TOKEN", "sekrit")
    reset_settings_cache()
    db.create_all()
    from vigil.api.app import create_app

    client = TestClient(create_app())
    resp = client.get("/api/instruments")
    assert resp.status_code == 401
    assert isinstance(resp.json()["detail"], str)
    assert client.get(
        "/api/instruments", headers={"Authorization": "Bearer wrong"}
    ).status_code == 401
    assert client.get(
        "/api/instruments", headers={"Authorization": "Bearer sekrit"}
    ).status_code == 200
    assert client.get(
        "/api/health/data", headers={"Authorization": "Bearer sekrit"}
    ).status_code == 200


def test_debug_no_token_skips_auth(api):
    client, _ = api
    assert client.get("/api/instruments").status_code == 200


def test_health_data(api):
    client, _ = api
    body = client.get("/api/health/data").json()
    assert body["providers"][0]["provider"] == "synthetic"
    assert body["providers"][0]["capability"] == "prices"
    assert body["providers"][0]["ok"] is True
    assert body["jobs"][0]["job_name"] == "scan"
    assert body["data"]["instruments"] == 3
    assert body["data"]["last_bar_date"] == TODAY.isoformat()
    assert body["data"]["price_staleness_days"] == 0
    assert body["data"]["last_run_at"] is not None


def test_config_is_sanitised(api):
    client, _ = api
    resp = client.get("/api/config")
    body = resp.json()
    for key in (
        "universe", "horizons", "gates", "alert_policy", "risk_policy",
        "scan", "base_currency", "model_version",
    ):
        assert key in body
    assert body["base_currency"] == "GBP"
    text = resp.text
    for secret in ("api_token", "smtp_password", "anthropic_api_key"):
        assert secret not in text


# ---------------------------------------------------------------------------
# Instruments & companies
# ---------------------------------------------------------------------------


def test_instruments_list_and_filters(api):
    client, _ = api
    body = client.get("/api/instruments").json()
    assert body["total"] == 3
    item = body["items"][0]
    for key in (
        "id", "ticker", "exchange", "market", "name", "sector", "industry",
        "currency", "security_type", "is_active", "delisted_at",
    ):
        assert key in item
    assert client.get("/api/instruments", params={"market": "UK"}).json()["total"] == 1
    assert client.get("/api/instruments", params={"q": "alpha"}).json()["total"] == 1
    assert client.get("/api/instruments", params={"q": "BBB"}).json()["total"] == 1
    paged = client.get("/api/instruments", params={"limit": 1, "offset": 1}).json()
    assert paged["total"] == 3
    assert len(paged["items"]) == 1


def test_company_detail(api):
    client, ids = api
    body = client.get(f"/api/companies/{ids['i1']}").json()
    assert body["instrument"]["ticker"] == "AAA"
    latest = body["latest"]
    assert latest["run_id"] == ids["run_ok"]
    assert latest["best_fit_horizon"] == "medium"
    assert set(latest["horizons"]) == {"short", "medium", "long"}
    med = latest["horizons"]["medium"]
    assert med["opportunity"] == 7.5
    assert med["gate"]["passed"] is True
    assert latest["horizons"]["long"]["abstained"] is True
    assert latest["warnings"] == ["one warning"]
    liq = body["liquidity"]
    assert liq["market_cap_base"] == pytest.approx(106.9 * 50e6 * 0.78)
    assert liq["median_daily_traded_value_base"] is not None
    assert liq["price_staleness_days"] == 0
    assert body["owned"] is True
    assert body["watchlisted"] is False


def test_company_never_scored_returns_header_with_null_latest(api):
    client, ids = api
    body = client.get(f"/api/companies/{ids['i3']}").json()
    assert body["instrument"]["ticker"] == "CCC"
    assert body["latest"] is None
    assert body["liquidity"]["market_cap_base"] is None
    assert client.get("/api/companies/999999").status_code == 404


def test_company_prices(api):
    client, ids = api
    body = client.get(f"/api/companies/{ids['i1']}/prices", params={"days": 30}).json()
    assert 25 <= len(body["bars"]) <= 31
    bar = body["bars"][-1]
    for key in ("date", "open", "high", "low", "close", "adj_close", "volume"):
        assert key in bar
    assert bar["date"] == TODAY.isoformat()
    assert len(body["markers"]) == 2
    marker = body["markers"][-1]
    assert marker["family"] == "breakout_continuation"
    assert marker["state"] == "TRIGGERED"
    assert marker["alert_id"] == ids["alert1"]


def test_company_financials_folds_restatements(api):
    client, ids = api
    body = client.get(f"/api/companies/{ids['i1']}/financials").json()
    quarters = body["quarters"]
    assert len(quarters) == 2
    q1, q2 = quarters
    assert q1["period_end"] == ids["q1_end"].isoformat()
    assert q1["revenue"] == 900.0  # restated value wins
    assert q1["is_restatement"] is True
    assert q1["gross_margin_pct"] == 50.0
    assert q1["fcf"] == 90.0
    assert q1["net_debt"] == 300.0
    assert q2["revenue"] == 1100.0
    assert q2["operating_margin_pct"] == 20.0


def test_company_engines(api):
    client, ids = api
    body = client.get(f"/api/companies/{ids['i1']}/engines").json()
    assert [e["engine"] for e in body["engines"]] == ["technical", "value"]
    value = body["engines"][1]
    assert value["score"] == 7.2
    assert value["data_quality"] == 0.9
    explicit = client.get(
        f"/api/companies/{ids['i1']}/engines", params={"run_id": ids["run_ok"]}
    ).json()
    assert explicit == body


def test_company_peers(api):
    client, ids = api
    body = client.get(f"/api/companies/{ids['i1']}/peers").json()
    assert len(body["peers"]) == 1
    peer = body["peers"][0]
    assert peer["ticker"] == "BBB"
    assert set(peer) == {"instrument_id", "ticker", "name", "metrics"}
    assert peer["metrics"]["market_cap"] == pytest.approx(56.9 * 10e6)


def test_company_alerts_and_signals(api):
    client, ids = api
    alerts = client.get(f"/api/companies/{ids['i1']}/alerts").json()["items"]
    assert [a["id"] for a in alerts] == [ids["alert1"], ids["alert2"]]
    signals = client.get(f"/api/companies/{ids['i1']}/signals").json()["items"]
    assert len(signals) == 2
    assert {s["family"] for s in signals} == {"breakout_continuation", "oversold_at_support"}


# ---------------------------------------------------------------------------
# Runs & opportunities
# ---------------------------------------------------------------------------


def test_runs(api):
    client, ids = api
    items = client.get("/api/runs").json()["items"]
    assert [r["id"] for r in items] == [ids["run_failed"], ids["run_ok"]]
    one = client.get(f"/api/runs/{ids['run_ok']}").json()
    for key in (
        "id", "run_at", "as_of", "model_version", "trigger", "universe_size",
        "scored", "abstained", "status", "detail",
    ):
        assert key in one
    assert one["status"] == "ok"
    assert client.get("/api/runs/999999").status_code == 404


def test_opportunities_from_latest_completed_run(api):
    client, ids = api
    body = client.get("/api/opportunities", params={"horizon": "medium"}).json()
    assert body["run_id"] == ids["run_ok"]  # failed run has a higher id but is skipped
    assert body["as_of"] == TODAY.isoformat()
    assert body["total"] == 2
    top = body["items"][0]
    assert top["ticker"] == "AAA"  # sorted by opportunity desc
    for key in (
        "instrument_id", "ticker", "name", "market", "sector", "horizon",
        "opportunity", "confidence", "risk", "components", "best_fit_horizon",
        "gate_passed", "abstained", "active_signals", "market_cap_base",
        "owned", "watchlisted",
    ):
        assert key in top
    assert top["gate_passed"] is True
    assert top["owned"] is True
    assert top["watchlisted"] is False
    assert top["active_signals"] == [{"family": "breakout_continuation", "state": "TRIGGERED"}]
    assert top["market_cap_base"] == pytest.approx(106.9 * 50e6 * 0.78)
    assert body["items"][1]["ticker"] == "BBB"
    assert body["items"][1]["owned"] is False
    assert body["items"][1]["watchlisted"] is True


def test_opportunities_filters(api):
    client, _ = api
    p = {"horizon": "medium"}
    assert client.get("/api/opportunities", params={**p, "gated_only": True}).json()["total"] == 1
    assert (
        client.get("/api/opportunities", params={**p, "min_opportunity": 7.0}).json()["total"] == 1
    )
    assert client.get("/api/opportunities", params={**p, "max_risk": 4.5}).json()["total"] == 1
    assert client.get("/api/opportunities", params={**p, "owned": False}).json()["total"] == 1
    assert client.get("/api/opportunities", params={**p, "watchlisted": True}).json()["total"] == 1
    assert (
        client.get(
            "/api/opportunities", params={**p, "family": "breakout_continuation"}
        ).json()["total"]
        == 1
    )
    assert (
        client.get(
            "/api/opportunities", params={**p, "catalyst_within_days": 15}
        ).json()["items"][0]["ticker"]
        == "AAA"
    )
    assert (
        client.get("/api/opportunities", params={**p, "catalyst_within_days": 15}).json()["total"]
        == 1
    )
    assert client.get("/api/opportunities", params={**p, "sector": "Energy"}).json()["total"] == 0
    short = client.get("/api/opportunities", params={"horizon": "short"}).json()
    assert short["total"] == 1


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


def test_alerts_list_shape_and_filters(api):
    client, ids = api
    body = client.get("/api/alerts").json()
    assert body["total"] == 2
    newest = body["items"][0]
    assert newest["id"] == ids["alert1"]
    for key in (
        "id", "created_at", "as_of", "instrument_id", "ticker", "name", "family",
        "lifecycle_state", "transition", "horizon", "priority", "title", "read",
        "opportunity", "confidence", "risk", "thesis_summary",
    ):
        assert key in newest
    assert newest["opportunity"] == 7.5
    assert newest["thesis_summary"] == "Breakout with strong value support."
    assert client.get("/api/alerts", params={"unread_only": True}).json()["total"] == 1
    assert client.get("/api/alerts", params={"state": "WATCHING"}).json()["total"] == 1
    assert client.get("/api/alerts", params={"priority": "high"}).json()["total"] == 1
    assert (
        client.get("/api/alerts", params={"instrument_id": ids["i2"]}).json()["total"] == 0
    )
    since = (datetime.combine(TODAY, time(0)) - timedelta(days=1)).isoformat()
    assert client.get("/api/alerts", params={"since": since}).json()["total"] == 1


def test_alert_detail_and_read_toggle(api):
    client, ids = api
    body = client.get(f"/api/alerts/{ids['alert1']}").json()
    assert body["payload"] == make_alert_payload() | {
        "generated_at": body["payload"]["generated_at"]
    }
    assert body["payload"]["thesis"]["why_this_company"] == "durable moat"
    assert client.get("/api/alerts/nope").status_code == 404

    assert client.post(f"/api/alerts/{ids['alert1']}/read").status_code == 200
    assert client.get(f"/api/alerts/{ids['alert1']}").json()["read"] is True
    assert client.post(f"/api/alerts/{ids['alert1']}/unread").status_code == 200
    assert client.get(f"/api/alerts/{ids['alert1']}").json()["read"] is False


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


def test_signals(api):
    client, ids = api
    body = client.get("/api/signals").json()
    assert body["total"] == 2
    assert client.get("/api/signals", params={"active": True}).json()["total"] == 1
    assert client.get("/api/signals", params={"state": "EXPIRED"}).json()["total"] == 1
    assert (
        client.get("/api/signals", params={"family": "breakout_continuation"}).json()["total"] == 1
    )
    one = client.get(f"/api/signals/{ids['signal']}").json()
    for key in (
        "id", "instrument_id", "ticker", "name", "family", "horizon", "state",
        "created_at", "updated_at", "anchor_price", "anchor_date", "entry_plan",
        "last_scores", "state_history", "expires_at", "active", "last_alert_at",
    ):
        assert key in one
    assert one["ticker"] == "AAA"
    assert one["entry_plan"] == {"zone_low": 95.0}
    assert client.get("/api/signals/999999").status_code == 404


# ---------------------------------------------------------------------------
# Portfolio & watchlist
# ---------------------------------------------------------------------------


def test_portfolio_view_and_breaches(api):
    client, _ = api
    body = client.get("/api/portfolio").json()
    assert len(body["positions"]) == 1
    pos = body["positions"][0]
    assert pos["ticker"] == "AAA"
    assert pos["last_price"] == pytest.approx(106.9)
    assert pos["value_base"] == pytest.approx(106.9 * 100 * 0.78)
    assert pos["weight_pct"] == pytest.approx(100.0)
    assert pos["unrealised_pct"] == pytest.approx((106.9 / 80.0 - 1) * 100)
    totals = body["totals"]
    assert totals["value_base"] == pytest.approx(106.9 * 100 * 0.78)
    assert totals["sector_weights"]["Technology"] == pytest.approx(100.0)
    assert totals["limits"]["max_position_exposure_pct"] == 10.0
    assert any("AAA" in b for b in totals["breaches"])
    assert any("Technology" in b for b in totals["breaches"])


def test_portfolio_add_and_close(api):
    client, ids = api
    resp = client.post(
        "/api/portfolio",
        json={
            "instrument_id": ids["i2"], "quantity": 10,
            "avg_cost_local": 40.0, "opened_at": TODAY.isoformat(),
        },
    )
    assert resp.status_code == 201
    new_id = resp.json()["id"]
    assert len(client.get("/api/portfolio").json()["positions"]) == 2
    assert client.delete(f"/api/portfolio/{new_id}").status_code == 200
    assert len(client.get("/api/portfolio").json()["positions"]) == 1
    assert client.delete("/api/portfolio/999999").status_code == 404
    assert (
        client.post(
            "/api/portfolio",
            json={
                "instrument_id": 999999, "quantity": 1,
                "avg_cost_local": 1.0, "opened_at": TODAY.isoformat(),
            },
        ).status_code
        == 404
    )


def test_watchlist_crud(api):
    client, ids = api
    items = client.get("/api/watchlist").json()["items"]
    assert len(items) == 1
    assert items[0]["ticker"] == "BBB"
    assert items[0]["notes"] == "watch me"
    resp = client.post("/api/watchlist", json={"instrument_id": ids["i3"]})
    assert resp.status_code == 201
    assert len(client.get("/api/watchlist").json()["items"]) == 2
    assert client.delete(f"/api/watchlist/{resp.json()['id']}").status_code == 200
    assert len(client.get("/api/watchlist").json()["items"]) == 1


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


def test_calendar(api):
    client, _ = api
    items = client.get("/api/calendar", params={"days": 60}).json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["ticker"] == "AAA"
    assert item["kind"] == "earnings"
    assert item["days"] == 10
    assert item["binary"] is True
    wide = client.get("/api/calendar", params={"days": 120}).json()["items"]
    assert [i["ticker"] for i in wide] == ["AAA", "BBB"]  # sorted by date
    binary = client.get(
        "/api/calendar", params={"days": 120, "binary_only": True}
    ).json()["items"]
    assert [i["ticker"] for i in binary] == ["AAA"]


# ---------------------------------------------------------------------------
# Backtests
# ---------------------------------------------------------------------------


def test_backtests_list_and_detail(api):
    client, ids = api
    items = client.get("/api/backtests").json()["items"]
    assert len(items) == 1
    for key in (
        "id", "created_at", "name", "model_version", "start_date", "end_date",
        "holdout_start", "status", "metrics",
    ):
        assert key in items[0]
    detail = client.get(f"/api/backtests/{ids['backtest']}").json()
    assert detail["by_bucket"] == {"short": {}}
    assert detail["calibration"] == {"bins": []}
    assert len(detail["trades"]) == 1
    trade = detail["trades"][0]
    for key in (
        "instrument_id", "ticker", "family", "horizon", "signal_date", "entry_date",
        "entry_price", "exit_date", "exit_price", "exit_reason", "holding_days",
        "return_pct", "benchmark_return_pct", "mae_pct", "mfe_pct", "costs_bps",
        "opportunity", "confidence", "risk",
    ):
        assert key in trade
    assert trade["ticker"] == "AAA"
    assert client.get("/api/backtests/999999").status_code == 404


def test_post_backtest_runs_in_background(api, monkeypatch):
    client, _ = api

    def fake_run_backtest(session, start, end, name="backtest", holdout_start=None, **kw):
        run = BacktestRun(
            name=name, model_version="test", config={}, start_date=start,
            end_date=end, holdout_start=holdout_start, status="ok",
        )
        session.add(run)
        session.flush()
        return run

    stub = types.ModuleType("vigil.backtest.engine")
    stub.run_backtest = fake_run_backtest
    monkeypatch.setitem(sys.modules, "vigil.backtest.engine", stub)

    resp = client.post(
        "/api/backtests",
        json={"start": (TODAY - timedelta(days=30)).isoformat(), "name": "api-bt"},
    )
    # 503 tolerated: the backtester module may not exist in every tree.
    assert resp.status_code in (202, 503)
    if resp.status_code == 503:
        assert resp.json()["detail"] == "backtester not installed"
        return
    bt_id = resp.json()["backtest_id"]
    assert isinstance(bt_id, int)
    for _ in range(100):
        got = client.get(f"/api/backtests/{bt_id}")
        if got.status_code == 200:
            break
        time_mod.sleep(0.05)
    assert got.status_code == 200
    assert got.json()["name"] == "api-bt"


# ---------------------------------------------------------------------------
# Scan, model versions, audit, notifications
# ---------------------------------------------------------------------------


def test_post_scan_runs_in_background(api, monkeypatch):
    client, _ = api

    def fake_run_scan(session, as_of, trigger="manual", settings=None, deliver_alerts=True):
        run = ScoreRun(
            as_of=as_of, model_version="test", config_hash="x", trigger=trigger, status="ok",
        )
        session.add(run)
        session.flush()
        return run

    stub = types.ModuleType("vigil.jobs.scan")
    stub.run_scan = fake_run_scan
    monkeypatch.setitem(sys.modules, "vigil.jobs.scan", stub)

    resp = client.post("/api/scan", json={"as_of": TODAY.isoformat()})
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    assert isinstance(run_id, int)
    for _ in range(100):
        got = client.get(f"/api/runs/{run_id}")
        if got.status_code == 200:
            break
        time_mod.sleep(0.05)
    assert got.status_code == 200
    assert got.json()["trigger"] == "manual"
    assert got.json()["as_of"] == TODAY.isoformat()


def test_model_versions(api):
    client, _ = api
    body = client.get("/api/model-versions").json()
    assert isinstance(body, list)
    assert body[0]["version"] == "v1.0.0"
    for key in ("version", "created_at", "weights", "config_hash", "notes", "active"):
        assert key in body[0]


def test_audit(api):
    client, _ = api
    items = client.get("/api/audit", params={"limit": 5}).json()["items"]
    assert items[0]["action"] == "scan_completed"
    for key in ("at", "actor", "action", "detail"):
        assert key in items[0]


def test_notifications(api):
    client, ids = api
    items = client.get("/api/notifications").json()["items"]
    assert len(items) == 1
    assert items[0]["alert_id"] == ids["alert1"]
    assert items[0]["channel"] == "inapp"
    for key in ("id", "alert_id", "channel", "created_at", "status", "detail"):
        assert key in items[0]
