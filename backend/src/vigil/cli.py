"""Vigil CLI.

    vigil seed                 # ingest the synthetic demo world
    vigil scan [--as-of DATE]  # run a full research scan
    vigil serve                # run the API (uvicorn)
    vigil schedule             # run the scheduler in the foreground
    vigil backtest             # point-in-time backtest over the stored world
    vigil alerts               # print recent alerts
    vigil health               # provider/data health summary

Research support only — there is no order placement anywhere in this tool.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime

import typer

from vigil.config import get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _setup_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _parse_date(value: str | None) -> date:
    if value is None:
        from vigil.providers.synthetic.universe import WORLD_NOW

        settings = get_settings()
        if settings.provider_price == "synthetic":
            return WORLD_NOW
        return datetime.now().date()
    return date.fromisoformat(value)


@app.command()
def seed(
    start: str = typer.Option("2020-07-01", help="History start date"),
    end: str = typer.Option(None, help="History end date (default: provider 'now')"),
) -> None:
    """Ingest the configured providers' full history (synthetic by default)."""
    _setup_logging()
    from vigil.db import create_all, session_scope
    from vigil.jobs.ingest_all import ingest_universe

    create_all()
    end_d = _parse_date(end)
    with session_scope() as session:
        detail = ingest_universe(session, date.fromisoformat(start), end_d)
    typer.echo(json.dumps(detail, indent=2, default=str))


@app.command()
def scan(
    as_of: str = typer.Option(None, help="Scan date (default: latest data date)"),
    trigger: str = typer.Option("manual"),
) -> None:
    """Run a full research scan: scores, signals, alerts."""
    _setup_logging()
    from vigil.db import create_all, session_scope
    from vigil.jobs.scan import expire_stale_watches, run_scan

    create_all()
    d = _parse_date(as_of)
    with session_scope() as session:
        run = run_scan(session, d, trigger=trigger)
        expired = expire_stale_watches(session, d)
        typer.echo(
            json.dumps(
                {
                    "run_id": run.id, "as_of": str(run.as_of), "universe": run.universe_size,
                    "scored": run.scored, "fully_abstained": run.abstained,
                    "alerts": run.detail.get("alerts"), "expired_watches": expired,
                    "model_version": run.model_version,
                },
                indent=2,
            )
        )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(False),
) -> None:
    """Run the API server."""
    import uvicorn

    _setup_logging()
    uvicorn.run("vigil.api.app:create_app", host=host, port=port, reload=reload, factory=True)


@app.command()
def schedule() -> None:
    """Run the scheduler in the foreground (EOD scans, digests, reviews)."""
    _setup_logging()
    from vigil.jobs.scheduler import run_scheduler

    run_scheduler()


@app.command()
def backtest(
    start: str = typer.Option("2022-01-01"),
    end: str = typer.Option(None),
    holdout_start: str = typer.Option(None, help="Untouched out-of-sample period start"),
    name: str = typer.Option("cli-backtest"),
    step_days: int = typer.Option(5, help="Trading-day step between scan dates"),
) -> None:
    """Point-in-time backtest of the full signal lifecycle."""
    _setup_logging()
    from vigil.backtest.engine import run_backtest
    from vigil.db import create_all, session_scope

    create_all()
    with session_scope() as session:
        run = run_backtest(
            session,
            start=date.fromisoformat(start),
            end=_parse_date(end),
            holdout_start=date.fromisoformat(holdout_start) if holdout_start else None,
            name=name,
            step_days=step_days,
        )
        typer.echo(json.dumps({"run_id": run.id, "metrics": run.metrics}, indent=2, default=str))


@app.command()
def alerts(limit: int = typer.Option(10)) -> None:
    """Print the most recent alerts."""
    _setup_logging()
    from sqlalchemy import select

    from vigil.db import create_all, session_scope
    from vigil.models import Alert

    create_all()
    with session_scope() as session:
        rows = session.execute(
            select(Alert).order_by(Alert.created_at.desc()).limit(limit)
        ).scalars().all()
        for row in rows:
            typer.echo(f"{row.created_at:%Y-%m-%d %H:%M} [{row.priority:>6}] {row.title}")
        if not rows:
            typer.echo("no alerts yet — run `vigil scan`")


@app.command()
def probe(ticker: str = typer.Argument("AAPL")) -> None:
    """Diagnose data fetching for ONE ticker: shows exactly what the
    configured price provider returns or the full error it raises."""
    _setup_logging()
    from datetime import timedelta

    from vigil.providers.base import CapabilityUnavailable, ProviderError
    from vigil.providers.registry import get_provider

    settings = get_settings()
    end = datetime.now().date()
    start = end - timedelta(days=30)
    typer.echo(f"price provider: {settings.provider_price}")
    try:
        prov = get_provider("prices")
        result = prov.fetch_bars(ticker, start, end)
        typer.echo(f"OK — {len(result.records)} daily bars for {ticker}")
        if result.records:
            last = result.records[-1]
            typer.echo(f"latest: {last.bar_date} close {last.close} {last.currency}")
        for w in result.warnings[:5]:
            typer.echo(f"warning: {w}")
        if not result.records:
            typer.echo("NO BARS RETURNED — provider responded but with no data. "
                       "Check the ticker spelling for this provider.")
    except (CapabilityUnavailable, ProviderError) as exc:
        typer.echo(f"FAILED: {exc}")
        typer.echo(
            "\nIf this is stooq: open "
            f"https://stooq.com/q/d/l/?s={ticker.lower()}.us&i=d in your web "
            "browser. A CSV download = your network is fine and this needs a "
            "code fix; an error page = stooq blocks/limits your network "
            "(common on corporate networks and VPNs) — use another provider."
        )
    # FX probe rides along: base-currency conversion needs it.
    try:
        macro = get_provider("macro")
        fx = macro.fetch_fx([("USD", "GBP")], start, end)
        typer.echo(f"fx USD/GBP: {len(fx.records)} rates"
                   + (f", latest {fx.records[-1].rate}" if fx.records else ""))
        for w in fx.warnings[:3]:
            typer.echo(f"fx warning: {w}")
    except (CapabilityUnavailable, ProviderError) as exc:
        typer.echo(f"fx FAILED: {exc}")


@app.command()
def health() -> None:
    """Provider and data health summary."""
    _setup_logging()
    from sqlalchemy import func, select

    from vigil.db import create_all, session_scope
    from vigil.models import ProviderHealthRecord

    create_all()
    with session_scope() as session:
        sub = (
            select(func.max(ProviderHealthRecord.id))
            .group_by(ProviderHealthRecord.provider, ProviderHealthRecord.capability)
        )
        rows = session.execute(
            select(ProviderHealthRecord).where(ProviderHealthRecord.id.in_(sub))
        ).scalars().all()
        for r in rows:
            status = "OK " if r.ok else ("--" if not r.configured else "ERR")
            typer.echo(f"[{status}] {r.provider:>12} {r.capability:<14} {r.message[:80]}")


if __name__ == "__main__":
    app()
