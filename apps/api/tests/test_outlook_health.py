"""The Outlook sync warning and /api/health (SPEC §5.4 "Outlook sync warning")."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jiak_api import models
from jiak_api.clock import Now
from jiak_api.db import Database
from jiak_api.main import create_app
from jiak_api.outlook_health import outlook_health

NOW = datetime(2026, 11, 11, 6, 30, tzinfo=UTC)  # 14:30 in Singapore
TODAY = Now(today="2026-11-11", minute=14 * 60 + 30)


def set_state(database: Database, **fields: object) -> None:
    with database.make_session() as db:
        state = db.get(models.OutlookState, 1) or models.OutlookState(id=1, last_error="")
        for name, value in fields.items():
            setattr(state, name, value)
        db.merge(state)
        db.commit()


def warning(database: Database, sweep_on: bool = True, secret: str | None = None) -> str:
    with database.make_session() as db:
        return outlook_health(db, NOW, sweep_on=sweep_on, secret_expires=secret).warning


def test_all_well_means_no_warning(database: Database) -> None:
    assert warning(database) == ""  # nothing recorded yet
    set_state(database, last_write_ok=NOW, last_read_ok=NOW)
    assert warning(database) == ""


def test_a_failed_attempt_after_the_last_success(database: Database) -> None:
    set_state(
        database,
        last_write_ok=NOW - timedelta(minutes=25),
        last_error="Writing 2026-12-01 to Outlook failed: timeout",
        last_error_at=NOW - timedelta(minutes=2),
    )
    assert warning(database) == (
        "Outlook not updated since 14:05: Writing 2026-12-01 to Outlook failed: timeout"
    )
    set_state(database, last_read_ok=NOW)  # it worked again since
    assert warning(database) == ""


def test_never_reached(database: Database) -> None:
    set_state(database, last_error="Reading Outlook failed: no network", last_error_at=NOW)
    assert warning(database) == "Outlook not reached yet: Reading Outlook failed: no network"


def test_an_old_success_shows_its_date(database: Database) -> None:
    set_state(
        database,
        last_read_ok=NOW - timedelta(days=1),
        last_error="x",
        last_error_at=NOW,
    )
    assert warning(database) == "Outlook not updated since 2026-11-10 14:30: x"


def test_changes_waiting_too_long(database: Database) -> None:
    with database.make_session() as db:
        db.add(models.OutlookPending(date="2026-12-01", since=NOW - timedelta(minutes=5)))
        db.commit()
    assert warning(database) == ""  # 5 min: still fine
    with database.make_session() as db:
        db.add(models.OutlookPending(date="2026-12-02", since=NOW - timedelta(minutes=12)))
        db.commit()
    assert warning(database) == "Changes waiting to go to Outlook since 14:18."


def test_outlook_not_read_for_a_while(database: Database) -> None:
    set_state(database, last_read_ok=NOW - timedelta(minutes=45))
    assert warning(database) == "Outlook not read since 13:45."
    assert warning(database, sweep_on=False) == ""  # tools and tests don't sweep


def test_secret_expiry_reminder(database: Database) -> None:
    assert warning(database, secret="2027-01-31") == ""
    assert warning(database, secret="2026-12-01") == (
        "The app's Outlook sign-in expires on 2026-12-01: ask the M365 admin for a new secret."
    )
    assert warning(database, secret="2026-11-11") == (
        "The app's Outlook sign-in expired on 2026-11-11: ask the M365 admin for a new secret."
    )


# --- on the pages and for an uptime checker ------------------------------------------------------


def test_banner_and_health_while_all_is_well(database: Database) -> None:
    client = TestClient(create_app(database, clock=lambda: TODAY))
    page = client.get("/enquiry")
    assert 'hx-get="/outlook-status"' in page.text
    assert client.get("/outlook-status").text == ""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"
    assert response.json()["outlook"]["ok"] is True


def test_banner_and_health_with_a_problem(database: Database) -> None:
    set_state(database, last_error="Reading Outlook failed: <timeout>", last_error_at=NOW)
    client = TestClient(create_app(database, clock=lambda: TODAY))
    banner = client.get("/outlook-status").text
    assert banner == (
        '<div class="banner" role="alert">'
        "Outlook not reached yet: Reading Outlook failed: &lt;timeout&gt;</div>"
    )
    response = client.get("/api/health")
    assert response.status_code == 503
    outlook = response.json()["outlook"]
    assert outlook["ok"] is False
    assert outlook["lastError"] == "Reading Outlook failed: <timeout>"
    assert outlook["lastReadOk"] is None


def test_health_when_the_database_is_down(tmp_path: Path) -> None:
    broken = Database(f"sqlite:///{tmp_path / 'no-such-folder' / 'x.db'}")
    client = TestClient(create_app(broken, clock=lambda: TODAY))
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["database"].startswith("error: ")


def test_a_wrong_secret_expiry_date_stops_the_app(database: Database) -> None:
    with pytest.raises(ValueError, match="AZURE_CLIENT_SECRET_EXPIRES must be a date"):
        create_app(database, secret_expires="31/01/2027")
