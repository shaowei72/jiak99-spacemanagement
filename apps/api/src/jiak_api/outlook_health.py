"""Is the Outlook link working? (SPEC §5.4 "Outlook sync warning").

- `outlook_health` works out the warning, if any, from what the sync and the sweep recorded.
- Every page loads `/outlook-status` (a small HTMX request, again every minute) and shows the
  warning as a banner at the top. Nothing shows while all is well.
- `GET /api/health` (no sign-in) gives the same plus whether the database answers, so an uptime
  checker can watch it: 200 when all is well, 503 otherwise.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from markupsafe import escape
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from engine.dates import add_days
from jiak_api import models
from jiak_api.clock import SINGAPORE
from jiak_api.db import Database

WAITING_WARN_MINUTES = 10  # changes not written to Outlook for this long -> warning
NOT_READ_WARN_MINUTES = 30  # Outlook not read for this long (while the sweep is on) -> warning
SECRET_WARN_DAYS = 30  # warn this many days before the app's Outlook secret expires

router = APIRouter()


@dataclass(frozen=True)
class OutlookHealth:
    """What the app knows about the Outlook link. `warning` is '' when all is well."""

    warning: str
    last_write_ok: datetime | None
    last_read_ok: datetime | None
    last_error: str
    waiting_since: datetime | None


def local_time(moment: datetime, now: datetime) -> str:
    """'14:05' for today, else '2026-11-10 14:05' (Singapore time)."""
    here = moment.astimezone(SINGAPORE)
    if here.date() == now.astimezone(SINGAPORE).date():
        return here.strftime("%H:%M")
    return here.strftime("%Y-%m-%d %H:%M")


def latest(*moments: datetime | None) -> datetime | None:
    """The latest of the moments given, ignoring None."""
    known = [moment for moment in moments if moment is not None]
    return max(known) if known else None


def outlook_health(
    db: Session,
    now: datetime,
    *,
    sweep_on: bool,
    secret_expires: str | None = None,
) -> OutlookHealth:
    """The warning to show, if any (SPEC §5.4). `now` is the current UTC time; `sweep_on` says
    whether this app reads Outlook regularly; `secret_expires` is AZURE_CLIENT_SECRET_EXPIRES."""
    state = db.get(models.OutlookState, 1)
    write_ok = state.last_write_ok if state else None
    read_ok = state.last_read_ok if state else None
    error = state.last_error if state else ""
    error_at = state.last_error_at if state else None
    waiting_since = db.scalar(select(func.min(models.OutlookPending.since)))

    warnings: list[str] = []
    last_ok = latest(write_ok, read_ok)
    if error_at is not None and (last_ok is None or error_at > last_ok):
        if last_ok is None:
            warnings.append(f"Outlook not reached yet: {error}")
        else:
            warnings.append(f"Outlook not updated since {local_time(last_ok, now)}: {error}")
    elif waiting_since is not None and now - waiting_since > timedelta(
        minutes=WAITING_WARN_MINUTES
    ):
        warnings.append(f"Changes waiting to go to Outlook since {local_time(waiting_since, now)}.")
    elif (
        sweep_on
        and read_ok is not None
        and now - read_ok > timedelta(minutes=NOT_READ_WARN_MINUTES)
    ):
        warnings.append(f"Outlook not read since {local_time(read_ok, now)}.")

    if secret_expires:
        today = now.astimezone(SINGAPORE).strftime("%Y-%m-%d")
        if today >= secret_expires:
            warnings.append(
                f"The app's Outlook sign-in expired on {secret_expires}: "
                "ask the M365 admin for a new secret."
            )
        elif add_days(today, SECRET_WARN_DAYS) >= secret_expires:
            warnings.append(
                f"The app's Outlook sign-in expires on {secret_expires}: "
                "ask the M365 admin for a new secret."
            )

    return OutlookHealth(
        warning=" ".join(warnings),
        last_write_ok=write_ok,
        last_read_ok=read_ok,
        last_error=error,
        waiting_since=waiting_since,
    )


def health_of(request: Request) -> OutlookHealth:
    """The Outlook health for this app (its database, sweep setting and secret expiry)."""
    database: Database = request.app.state.database
    with database.make_session() as db:
        return outlook_health(
            db,
            models.utc_now(),
            sweep_on=request.app.state.sweep_minutes > 0,
            secret_expires=request.app.state.secret_expires,
        )


@router.get("/outlook-status", response_class=HTMLResponse, include_in_schema=False)
def outlook_status(request: Request) -> HTMLResponse:
    """The warning banner for the top of every page (empty while all is well)."""
    health = health_of(request)
    if not health.warning:
        return HTMLResponse("", headers={"Cache-Control": "no-store"})
    banner = f'<div class="banner" role="alert">{escape(health.warning)}</div>'
    return HTMLResponse(banner, headers={"Cache-Control": "no-store"})


def iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


@router.get("/api/health")
def api_health(request: Request) -> JSONResponse:
    """For an uptime checker: 200 when the database answers and Outlook is fine, else 503."""
    database: Database = request.app.state.database
    body: dict[str, Any] = {}
    try:
        with database.make_session() as db:
            db.execute(text("SELECT 1"))
        body["database"] = "ok"
    except Exception as error:
        body["database"] = f"error: {error}"
        return JSONResponse(body, status_code=503)

    health = health_of(request)
    body["outlook"] = {
        "ok": not health.warning,
        "warning": health.warning,
        "lastWriteOk": iso(health.last_write_ok),
        "lastReadOk": iso(health.last_read_ok),
        "lastError": health.last_error,
        "waitingSince": iso(health.waiting_since),
    }
    return JSONResponse(body, status_code=200 if not health.warning else 503)
