"""In-process job scheduler (APScheduler 3.x, BlockingScheduler).

Schedules — all times UTC, driven entirely by ``settings.scan``:

* ``eod_scan``            cron, weekdays at ``eod_scan_utc_hour``: incremental
  ingest refresh → full scan on the latest bar date → expire stale watches.
* ``intraday_monitor``    interval every ``intraday_interval_minutes`` —
  registered only when ``scan_frequency == "intraday"``.
* ``daily_digest``        cron, daily at ``digest_utc_hour``.
* ``weekly_review``       cron, weekly on ``weekly_review_weekday``.
* ``catalyst_reminders``  cron, daily at ``digest_utc_hour``.

Every job opens its own session via ``session_scope`` and is wrapped in
JobRun bookkeeping; failures are recorded and surfaced through
``alerts.notify.notify_data_failure`` (in-app + webhook). A missed window
while the process is down simply shows up as a gap in ``job_runs`` and can
be re-run manually (see docs/LIMITATIONS.md).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vigil.config import Settings, get_settings
from vigil.db import session_scope
from vigil.models import JobRun, PriceBar

log = logging.getLogger(__name__)

# Incremental refresh re-pulls a few days behind the last stored bar so late
# corrections/backfills are picked up; first run bootstraps enough history
# for the long-horizon engines (~3 years).
INGEST_OVERLAP_DAYS = 5
BOOTSTRAP_DAYS = 3 * 365


def latest_bar_date(session: Session) -> date | None:
    """Most recent stored price-bar date across the whole universe."""
    return session.execute(select(func.max(PriceBar.bar_date))).scalar_one()


def _run_job(job_name: str, work: Callable[[Session], dict | None], settings: Settings) -> dict:
    """Run ``work`` in its own session with JobRun bookkeeping.

    The work session and the bookkeeping session are separate so a failed
    flush cannot poison the failure record. On exception the error is logged,
    recorded as a failed JobRun, and pushed via ``notify_data_failure``.
    """
    from vigil.alerts.notify import notify_data_failure

    started = datetime.now(UTC).replace(tzinfo=None)
    error: str | None = None
    try:
        with session_scope() as session:
            detail = work(session) or {}
    except Exception as exc:  # scheduler jobs must never crash the loop
        log.exception("scheduled job %s failed", job_name)
        error = f"{type(exc).__name__}: {exc}"
        detail = {"error": error[:400]}
    with session_scope() as session:
        session.add(
            JobRun(
                job_name=job_name,
                started_at=started,
                finished_at=datetime.now(UTC).replace(tzinfo=None),
                status="failed" if error else "ok",
                detail=detail,
            )
        )
        if error:
            notify_data_failure(session, job_name, error, settings)
    return detail


# --------------------------------------------------------------------------
# Job bodies (module-level so APScheduler can reference them cleanly)
# --------------------------------------------------------------------------


def _eod_work(session: Session, settings: Settings) -> dict:
    from vigil.jobs.ingest_all import ingest_universe
    from vigil.jobs.scan import expire_stale_watches, run_scan

    today = date.today()
    last = latest_bar_date(session)
    start = (
        last - timedelta(days=INGEST_OVERLAP_DAYS)
        if last is not None
        else today - timedelta(days=BOOTSTRAP_DAYS)
    )
    ingest_stats = ingest_universe(session, start, today)

    as_of = latest_bar_date(session)
    if as_of is None:
        raise RuntimeError("no price bars available after ingest refresh")
    run = run_scan(session, as_of, trigger="eod", settings=settings)
    expired = expire_stale_watches(session, as_of)
    return {
        "as_of": as_of.isoformat(),
        "run_id": run.id,
        "scored": run.scored,
        "abstained": run.abstained,
        "alerts": (run.detail or {}).get("alerts", 0),
        "expired_watches": expired,
        "ingest": ingest_stats,
    }


def eod_scan_job() -> dict:
    settings = get_settings()
    return _run_job("eod_scan", lambda s: _eod_work(s, settings), settings)


def _intraday_work(session: Session, settings: Settings) -> dict:
    from vigil.jobs.scan import run_scan

    as_of = latest_bar_date(session)
    if as_of is None:
        return {"skipped": "no price bars stored yet"}
    run = run_scan(session, as_of, trigger="intraday", settings=settings)
    return {
        "as_of": as_of.isoformat(),
        "run_id": run.id,
        "scored": run.scored,
        "alerts": (run.detail or {}).get("alerts", 0),
    }


def intraday_monitor_job() -> dict:
    settings = get_settings()
    return _run_job("intraday_monitor", lambda s: _intraday_work(s, settings), settings)


def daily_digest_job() -> dict:
    from vigil.jobs import digests

    settings = get_settings()
    return _run_job("daily_digest", lambda s: digests.send_daily_digest(s, settings), settings)


def weekly_review_job() -> dict:
    from vigil.jobs import digests

    settings = get_settings()
    return _run_job("weekly_review", lambda s: digests.send_weekly_review(s, settings), settings)


def catalyst_reminders_job() -> dict:
    from vigil.jobs import digests

    settings = get_settings()
    return _run_job(
        "catalyst_reminders", lambda s: digests.send_catalyst_reminders(s, settings), settings
    )


# --------------------------------------------------------------------------
# Scheduler wiring
# --------------------------------------------------------------------------


def build_scheduler(settings: Settings | None = None) -> BlockingScheduler:
    """Construct (but do not start) the scheduler for the given settings."""
    settings = settings or get_settings()
    scan = settings.scan
    defaults = {"coalesce": True, "misfire_grace_time": 3600, "max_instances": 1}
    sched = BlockingScheduler(timezone="UTC", job_defaults=defaults)

    sched.add_job(
        eod_scan_job,
        "cron",
        day_of_week="mon-fri",
        hour=scan.eod_scan_utc_hour,
        minute=0,
        id="eod_scan",
        name="EOD ingest refresh + scan",
    )
    if scan.scan_frequency == "intraday":
        sched.add_job(
            intraday_monitor_job,
            "interval",
            minutes=scan.intraday_interval_minutes,
            id="intraday_monitor",
            name="Intraday monitor re-scan",
        )
    sched.add_job(
        daily_digest_job,
        "cron",
        hour=scan.digest_utc_hour,
        minute=0,
        id="daily_digest",
        name="Daily digest",
    )
    sched.add_job(
        weekly_review_job,
        "cron",
        day_of_week=scan.weekly_review_weekday,
        hour=scan.digest_utc_hour,
        minute=15,
        id="weekly_review",
        name="Weekly portfolio/thesis review",
    )
    sched.add_job(
        catalyst_reminders_job,
        "cron",
        hour=scan.digest_utc_hour,
        minute=30,
        id="catalyst_reminders",
        name="Upcoming catalyst reminders",
    )
    return sched


def list_jobs(settings: Settings | None = None) -> list[dict]:
    """Schedule diagnostics: what would run, and on what trigger."""
    sched = build_scheduler(settings)
    return [
        {
            "id": job.id,
            "name": job.name,
            "trigger": str(job.trigger),
            "next_run": str(getattr(job, "next_run_time", None)),
        }
        for job in sched.get_jobs()
    ]


def run_scheduler(settings: Settings | None = None) -> None:
    """Run the scheduler in the foreground until interrupted."""
    settings = settings or get_settings()
    sched = build_scheduler(settings)
    for job in sched.get_jobs():
        log.info("scheduled %-20s trigger=%s", job.id, job.trigger)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):  # pragma: no cover - interactive exit
        log.info("scheduler stopped")
