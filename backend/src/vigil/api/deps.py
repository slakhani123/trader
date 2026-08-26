"""FastAPI dependencies and background-job plumbing.

* ``get_db`` — one ORM session per request, committed on success.
* ``require_auth`` — constant-time bearer-token check against
  ``settings.api_token``; skipped only when ``debug`` is on AND no token is
  configured (local dev), per docs/API_SPEC.md.
* ``spawn_job`` — run a persistence job (scan/backtest) in its own thread
  with its own session, returning the id of the row the job creates.
"""

from __future__ import annotations

import hmac
import logging
import threading
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session

from vigil.config import Settings, get_settings
from vigil.db import get_session_factory, session_scope

log = logging.getLogger("vigil.api")


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_config() -> Settings:
    return get_settings()


def require_auth(request: Request, settings: Settings = Depends(get_config)) -> None:
    token = settings.api_token
    if not token:
        if settings.debug:
            return  # local dev only: debug on and no token configured
        raise HTTPException(status_code=401, detail="API token not configured")
    header = request.headers.get("authorization", "")
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        credentials.strip().encode(), token.encode()
    ):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


def pagination(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> tuple[int, int]:
    return limit, offset


def spawn_job(
    row_model: type,
    runner: Callable[[Session], Any],
    *,
    name: str,
    timeout: float = 30.0,
) -> int | None:
    """Run ``runner`` in a daemon thread with its OWN session.

    ``run_scan``/``run_backtest`` both create their run row and flush it
    early, so an ``after_flush`` listener captures the row's real id as soon
    as it exists; the caller can return 202 with that id while the job keeps
    going. Returns ``None`` if the job dies before creating its row.
    """
    box: dict[str, int] = {}
    ready = threading.Event()

    def worker() -> None:
        try:
            with session_scope() as s:

                @sa_event.listens_for(s, "after_flush")
                def _capture(sess: Session, _ctx: Any) -> None:
                    if "id" in box:
                        return
                    for obj in sess.new:
                        if isinstance(obj, row_model) and getattr(obj, "id", None) is not None:
                            box["id"] = obj.id  # type: ignore[attr-defined]
                            ready.set()
                            return

                runner(s)
        except Exception:
            log.exception("background job %r failed", name)
        finally:
            ready.set()

    threading.Thread(target=worker, name=name, daemon=True).start()
    ready.wait(timeout=timeout)
    return box.get("id")
