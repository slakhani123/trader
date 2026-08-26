"""Shared fixtures: isolated SQLite DB per test session + settings reset."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

import vigil.db as db
from vigil.config import reset_settings_cache


@pytest.fixture()
def sqlite_env(tmp_path, monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("VIGIL_DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("VIGIL_DEBUG", "true")
    reset_settings_cache()
    db.dispose_engine()
    yield
    db.dispose_engine()
    reset_settings_cache()


@pytest.fixture()
def session(sqlite_env) -> Iterator[Session]:
    db.create_all()
    with db.session_scope() as s:
        yield s


@pytest.fixture(scope="session")
def seeded_db_url(tmp_path_factory) -> str:
    """One synthetic-world DB shared across the whole test session (seeding
    takes ~15s). Tests must treat it as read-only."""
    path = tmp_path_factory.mktemp("seed") / "seeded.db"
    url = f"sqlite:///{path}"
    prev = os.environ.get("VIGIL_DATABASE_URL")
    os.environ["VIGIL_DATABASE_URL"] = url
    reset_settings_cache()
    db.dispose_engine()
    try:
        from datetime import date

        from vigil.jobs.ingest_all import ingest_universe

        db.create_all()
        with db.session_scope() as s:
            ingest_universe(s, date(2020, 7, 1), date(2026, 8, 25))
    finally:
        if prev is None:
            os.environ.pop("VIGIL_DATABASE_URL", None)
        else:
            os.environ["VIGIL_DATABASE_URL"] = prev
        reset_settings_cache()
        db.dispose_engine()
    return url


@pytest.fixture()
def seeded_session(seeded_db_url, monkeypatch) -> Iterator[Session]:
    monkeypatch.setenv("VIGIL_DATABASE_URL", seeded_db_url)
    reset_settings_cache()
    db.dispose_engine()
    with db.session_scope() as s:
        yield s
    db.dispose_engine()
    reset_settings_cache()
