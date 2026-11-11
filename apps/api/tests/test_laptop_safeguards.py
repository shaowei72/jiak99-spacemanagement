"""The SQLite safeguards and the owner's name for the laptop phase (SPEC §5.5).

The cross-program write lock is tested in test_concurrency.py.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from jiak_api import models
from jiak_api.clock import Now
from jiak_api.config import load_config
from jiak_api.db import Database, DatabaseLocationError, check_database_location, sqlite_file
from jiak_api.main import create_app

TODAY = Now(today="2026-11-07", minute=8 * 60)
BOOKING = {
    "date": "2026-12-01",
    "start": "10:00",
    "type": "CLASSIC",
    "adults": 6,
    "customer": {"name": "Ong party"},
}


# --- where the database file may be --------------------------------------------------------------


def test_sqlite_file_of_a_url() -> None:
    assert sqlite_file("sqlite:///C:/Users/x/AppData/Local/Jiak99/jiak99.db") == Path(
        "C:/Users/x/AppData/Local/Jiak99/jiak99.db"
    )
    assert sqlite_file("sqlite:///./jiak99-dev.db") == Path("./jiak99-dev.db")
    assert sqlite_file("sqlite://") is None
    assert sqlite_file("sqlite:///:memory:") is None
    assert sqlite_file("postgresql+psycopg://jiak:jiak@127.0.0.1:5432/jiak") is None


def test_a_database_inside_onedrive_is_refused(tmp_path: Path) -> None:
    onedrive = tmp_path / "OneDrive - Jiak99"
    local = tmp_path / "AppData" / "Local" / "Jiak99"
    environ = {"OneDriveCommercial": str(onedrive), "OneDrive": str(onedrive)}

    with pytest.raises(DatabaseLocationError, match="inside OneDrive"):
        check_database_location(f"sqlite:///{onedrive / 'Documents' / 'jiak99.db'}", environ)
    check_database_location(f"sqlite:///{local / 'jiak99.db'}", environ)  # fine
    check_database_location("postgresql+psycopg://x@127.0.0.1/jiak", environ)  # not a file
    check_database_location(f"sqlite:///{onedrive / 'jiak99.db'}", {})  # no OneDrive here


def test_opening_a_database_in_onedrive_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OneDrive", str(tmp_path))
    with pytest.raises(DatabaseLocationError):
        Database(f"sqlite:///{tmp_path / 'jiak99.db'}")


# --- SQLite settings ---------------------------------------------------------------------------------


def test_sqlite_runs_in_wal_mode_with_a_busy_wait(database: Database) -> None:
    if not database.is_sqlite:
        pytest.skip("SQLite only")
    with database.make_session() as db:
        assert db.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert db.execute(text("PRAGMA busy_timeout")).scalar() == 30_000


# --- text limits -----------------------------------------------------------------------------------


def test_the_api_refuses_text_longer_than_the_database_keeps(database: Database) -> None:
    client = TestClient(create_app(database, clock=lambda: TODAY))
    too_long = {**BOOKING, "customer": {"name": "Ong", "phone": "9" * 51}}
    response = client.post("/api/bookings", json=too_long)
    assert response.status_code == 422
    assert "phone" in response.text
    just_right = {**BOOKING, "customer": {"name": "N" * 200, "company": "C" * 200}}
    assert client.post("/api/bookings", json=just_right).status_code == 201


def test_the_forms_refuse_text_longer_than_the_database_keeps(database: Database) -> None:
    client = TestClient(create_app(database, clock=lambda: TODAY))
    form = {
        "date": "2026-12-01",
        "start": "10:00",
        "type": "CLASSIC",
        "adults": "6",
        "children": "0",
        "name": "Ong party",
        "email": "e" * 201,
    }
    response = client.post("/enquiry/book", data=form)
    assert response.status_code == 422
    assert "The customer&#39;s email can be at most 200 characters." in response.text

    booked = client.post("/enquiry/book", data={**form, "email": "ong@example.com"})
    assert booked.status_code == 200
    with database.make_session() as db:
        booking_id = db.scalar(select(models.Booking.id))
    edit = {**form, "email": "ong@example.com", "company": "c" * 201}
    response = client.post(f"/bookings/{booking_id}/edit", data=edit)
    assert response.status_code == 422
    assert "The customer&#39;s company can be at most 200 characters." in response.text


# --- the owner's name in the change history -----------------------------------------------------


def test_changes_are_recorded_under_jiak_user(database: Database) -> None:
    client = TestClient(create_app(database, clock=lambda: TODAY, default_user="Owner Name"))
    assert client.post("/api/bookings", json=BOOKING).status_code == 201
    # A caller that says who it is still counts as that person.
    other = {**BOOKING, "start": "12:00"}
    assert client.post("/api/bookings", json=other, headers={"X-User": "ops@x"}).status_code == 201
    with database.make_session() as db:
        actors = sorted(db.scalars(select(models.AuditLog.actor)))
    assert actors == ["Owner Name", "ops@x"]


def test_jiak_user_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIAK_USER", "  Owner Name ")
    assert load_config().default_user == "Owner Name"
    monkeypatch.delenv("JIAK_USER")
    assert load_config().default_user == "dev"
