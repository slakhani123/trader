"""Scheduler wiring tests. The scheduler loop is never started here —
we only assert what ``build_scheduler``/``list_jobs`` would register."""

from __future__ import annotations

from sqlalchemy import select

import vigil.db as db
from vigil.config import ScanConfig, Settings
from vigil.jobs import scheduler
from vigil.models import JobRun, NotificationDelivery

BASE_IDS = {"eod_scan", "daily_digest", "weekly_review", "catalyst_reminders"}


def _settings(**scan_kwargs) -> Settings:
    return Settings(scan=ScanConfig(**scan_kwargs))


def test_list_jobs_eod_config() -> None:
    settings = _settings(
        scan_frequency="eod", eod_scan_utc_hour=21, digest_utc_hour=7,
        weekly_review_weekday=6,
    )
    jobs = {j["id"]: j for j in scheduler.list_jobs(settings)}

    assert set(jobs) == BASE_IDS  # no intraday monitor in EOD mode
    assert "day_of_week='mon-fri'" in jobs["eod_scan"]["trigger"]
    assert "hour='21'" in jobs["eod_scan"]["trigger"]
    assert "hour='7'" in jobs["daily_digest"]["trigger"]
    assert "day_of_week='6'" in jobs["weekly_review"]["trigger"]
    assert jobs["catalyst_reminders"]["trigger"].startswith("cron[")


def test_list_jobs_intraday_config_adds_monitor() -> None:
    settings = _settings(scan_frequency="intraday", intraday_interval_minutes=15)
    jobs = {j["id"]: j for j in scheduler.list_jobs(settings)}

    assert set(jobs) == BASE_IDS | {"intraday_monitor"}
    assert jobs["intraday_monitor"]["trigger"] == "interval[0:15:00]"
    # EOD scan is still scheduled alongside the intraday monitor.
    assert "hour='21'" in jobs["eod_scan"]["trigger"]


def test_list_jobs_respects_configured_hours() -> None:
    settings = _settings(eod_scan_utc_hour=22, digest_utc_hour=8, weekly_review_weekday=0)
    jobs = {j["id"]: j for j in scheduler.list_jobs(settings)}

    assert "hour='22'" in jobs["eod_scan"]["trigger"]
    assert "hour='8'" in jobs["daily_digest"]["trigger"]
    assert "day_of_week='0'" in jobs["weekly_review"]["trigger"]


def test_run_job_records_success(sqlite_env) -> None:
    db.create_all()
    detail = scheduler._run_job("daily_digest", lambda s: {"sent": 1}, Settings())

    assert detail == {"sent": 1}
    with db.session_scope() as s:
        job = s.execute(
            select(JobRun).where(JobRun.job_name == "daily_digest")
        ).scalars().one()
        assert job.status == "ok"
        assert job.detail == {"sent": 1}
        assert job.finished_at is not None
        assert s.execute(select(NotificationDelivery)).scalars().first() is None


def test_run_job_records_failure_and_notifies(sqlite_env) -> None:
    db.create_all()

    def boom(_session) -> dict:
        raise RuntimeError("provider exploded")

    detail = scheduler._run_job("eod_scan", boom, Settings())

    assert "provider exploded" in detail["error"]
    with db.session_scope() as s:
        job = s.execute(select(JobRun).where(JobRun.job_name == "eod_scan")).scalars().one()
        assert job.status == "failed"
        assert "provider exploded" in job.detail["error"]
        note = s.execute(select(NotificationDelivery)).scalars().one()
        assert note.channel == "inapp"
        assert note.alert_id is None
        assert note.detail.startswith("DATA FAILURE eod_scan")
