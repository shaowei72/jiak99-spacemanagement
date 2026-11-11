"""Hourly backups, `uv run restore` and the after-restore list (SPEC §5.6).

These tests use their own SQLite files (backups and restore are for the laptop's SQLite database),
whatever TEST_DATABASE_URL says.
"""

import socket
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from engine import Enquiry
from graph.fake import FakeGateway
from graph.model import CalendarEvent
from jiak_api import backup, bookings, models, restore
from jiak_api.clock import SINGAPORE, Now
from jiak_api.db import Database
from jiak_api.main import create_app
from jiak_api.migrate import upgrade_to_latest
from jiak_api.outlook_inbound import sweep
from jiak_api.outlook_sync import sync_dates
from jiak_api.restore_pages import NOT_IN_APP_NOTE

NOW = Now(today="2026-11-07", minute=8 * 60)
MOMENT = datetime(2026, 11, 11, 6, 30, tzinfo=UTC)  # 14:30 in Singapore
DATE = "2026-12-01"


@pytest.fixture
def url(tmp_path: Path) -> str:
    """A new, migrated SQLite database file (the laptop's database)."""
    address = f"sqlite:///{tmp_path / 'live' / 'jiak99.db'}"
    (tmp_path / "live").mkdir()
    upgrade_to_latest(address)
    return address


@pytest.fixture
def laptop(url: str) -> Iterator[Database]:
    database = Database(url)
    yield database
    database.engine.dispose()


def book(database: Database, name: str, start: int = 600, date: str = DATE) -> str:
    enquiry = Enquiry(date=date, start=start, ticket_type="CLASSIC", adults=6)
    with database.make_session() as db:
        booking = bookings.create_booking(
            database, db, enquiry, bookings.CustomerDetails(name=name), notes="", actor="t", now=NOW
        )
        return booking.id


def count_bookings(url: str) -> int:
    database = Database(url)
    try:
        with database.make_session() as db:
            return db.scalar(select(func.count()).select_from(models.Booking)) or 0
    finally:
        database.engine.dispose()


# --- backup names and the keep rule ------------------------------------------------------------


def test_backup_names() -> None:
    assert backup.backup_name(MOMENT) == "jiak99-2026-11-11-1430.db"
    assert backup.read_backup_name("jiak99-2026-11-11-1430.db") == datetime(
        2026, 11, 11, 14, 30, tzinfo=SINGAPORE
    )
    assert backup.read_backup_name("jiak99-2026-11-11-1430.db.partial") is None
    assert backup.read_backup_name("jiak99-2026-02-30-1430.db") is None
    assert backup.read_backup_name("notes.txt") is None


def test_the_keep_rule() -> None:
    now = datetime(2026, 11, 11, 14, 30, tzinfo=SINGAPORE)
    made = [now - timedelta(hours=hours) for hours in range(0, 24 * 40, 6)]  # every 6 h, 40 days
    files = [backup.BackupFile(Path(backup.backup_name(m)), m) for m in made]
    files.sort(key=lambda f: f.made)
    deleted = {f.made for f in backup.backups_to_delete(files, now)}
    kept = [f.made for f in files if f.made not in deleted]

    assert all(now - m <= timedelta(days=30) for m in kept)  # nothing older than 30 days
    assert {m for m in made if now - m <= timedelta(days=2)} <= set(kept)  # last 2 days: all
    older = [m for m in kept if now - m > timedelta(days=2)]
    days = [m.strftime("%Y-%m-%d") for m in older]
    assert len(days) == len(set(days))  # before that: one per day ...
    for day in set(days):  # ... the newest of that day
        newest = max(m for m in made if m.strftime("%Y-%m-%d") == day)
        assert newest in older


# --- making backups --------------------------------------------------------------------------------


def test_a_backup_is_a_checked_copy(url: str, laptop: Database, tmp_path: Path) -> None:
    book(laptop, "Ong party")
    folder = tmp_path / "OneDrive" / "Jiak99 backups"
    made = backup.backup_now(laptop, folder, MOMENT)
    assert made == folder / "jiak99-2026-11-11-1430.db"
    assert [p.name for p in folder.iterdir()] == ["jiak99-2026-11-11-1430.db"]  # no .partial left
    copy = sqlite3.connect(made)
    assert copy.execute("SELECT count(*) FROM bookings").fetchone()[0] == 1
    copy.close()
    with laptop.make_session() as db:
        state = db.get(models.BackupState, 1)
        assert state is not None
        assert (state.last_file, state.last_error) == ("jiak99-2026-11-11-1430.db", "")


def test_old_backups_are_deleted_after_a_new_one(laptop: Database, tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    folder.mkdir()
    old = folder / "jiak99-2026-09-01-1000.db"  # 71 days old
    old.write_bytes(b"")
    other = folder / "my notes.txt"
    other.write_text("not a backup")
    backup.backup_now(laptop, folder, MOMENT)
    assert not old.exists()
    assert other.exists()  # never touches other files


def test_a_failed_backup_is_recorded_and_shown(laptop: Database, tmp_path: Path) -> None:
    not_a_folder = tmp_path / "file"
    not_a_folder.write_text("x")
    assert backup.backup_now(laptop, not_a_folder, MOMENT) is None
    client = TestClient(create_app(laptop, clock=lambda: NOW, backup_dir=str(tmp_path / "b")))
    assert "Backup failed: " in client.get("/outlook-status").text
    assert client.get("/api/health").status_code == 503


def test_backups_need_the_sqlite_file(tmp_path: Path) -> None:
    with pytest.raises(backup.BackupError, match="SQLite"):
        backup.make_backup("postgresql+psycopg://x@127.0.0.1/jiak", tmp_path, MOMENT)
    with pytest.raises(backup.BackupError, match="doesn't exist"):
        backup.make_backup(f"sqlite:///{tmp_path / 'missing.db'}", tmp_path, MOMENT)


def test_a_backup_is_due_after_an_hour(laptop: Database, tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    assert backup.backup_due(folder, MOMENT)  # none yet
    backup.backup_now(laptop, folder, MOMENT)
    assert not backup.backup_due(folder, MOMENT + timedelta(minutes=59))
    assert backup.backup_due(folder, MOMENT + timedelta(minutes=60))


def test_the_app_backs_up_when_it_starts_and_when_it_closes(
    laptop: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "backups"
    times = iter([MOMENT, MOMENT, MOMENT + timedelta(minutes=5)])
    monkeypatch.setattr("jiak_api.main.utc_now", lambda: next(times))
    app = create_app(laptop, clock=lambda: NOW, backup_dir=str(folder))
    with TestClient(app):
        for _ in range(200):
            if list(folder.glob("jiak99-*.db")):
                break
            import time

            time.sleep(0.01)
    names = sorted(p.name for p in folder.glob("jiak99-*.db"))
    assert names == ["jiak99-2026-11-11-1430.db", "jiak99-2026-11-11-1435.db"]


def test_no_backup_for_two_hours_is_shown(laptop: Database) -> None:
    with laptop.make_session() as db:
        db.add(
            models.BackupState(
                id=1,
                last_ok=models.utc_now() - timedelta(hours=3),
                last_file="x",
                last_error="",
            )
        )
        db.commit()
    client = TestClient(create_app(laptop, clock=lambda: NOW, backup_dir="unused"))
    assert "No backup since " in client.get("/outlook-status").text
    without = TestClient(create_app(laptop, clock=lambda: NOW))  # backups off: no warning
    assert without.get("/outlook-status").text == ""


# --- restore -----------------------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_restore_puts_the_backup_back_and_pauses_outlook(
    url: str, laptop: Database, tmp_path: Path
) -> None:
    book(laptop, "Before the backup")
    made = backup.backup_now(laptop, tmp_path / "backups", MOMENT)
    assert made is not None
    book(laptop, "After the backup", start=720)
    laptop.engine.dispose()

    lines = restore.restore(made, url, MOMENT, port=free_port())

    assert lines[0].startswith("Restored jiak99-2026-11-11-1430.db into ")
    assert "jiak99-replaced-2026-11-11-143000.db" in lines[1]
    assert count_bookings(url) == 1
    replaced = tmp_path / "live" / "jiak99-replaced-2026-11-11-143000.db"
    assert count_bookings(f"sqlite:///{replaced}") == 2  # never deleted
    database = Database(url)
    with database.make_session() as db:
        state = db.get(models.OutlookState, 1)
        assert state is not None
        assert (state.sync_paused, state.restored_from) == (True, "jiak99-2026-11-11-1430.db")
    database.engine.dispose()


def test_restore_onto_a_new_laptop(tmp_path: Path, laptop: Database) -> None:
    made = backup.backup_now(laptop, tmp_path / "backups", MOMENT)
    assert made is not None
    new_laptop = f"sqlite:///{tmp_path / 'new' / 'jiak99.db'}"
    lines = restore.restore(made, new_laptop, MOMENT, port=free_port())
    assert "replaced" not in "\n".join(lines)
    assert (tmp_path / "new" / "jiak99.db").exists()


def test_restore_refuses_while_the_app_runs(url: str, tmp_path: Path) -> None:
    with socket.socket() as app:
        app.bind(("127.0.0.1", 0))
        app.listen()
        port = app.getsockname()[1]
        with pytest.raises(restore.RestoreError, match="still running"):
            restore.restore(tmp_path / "x.db", url, MOMENT, port=port)


def test_restore_refuses_bad_backups(url: str, tmp_path: Path) -> None:
    port = free_port()
    with pytest.raises(restore.RestoreError, match="There is no file"):
        restore.restore(tmp_path / "missing.db", url, MOMENT, port=port)
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE notes (x)").connection.close()
    with pytest.raises(restore.RestoreError, match="not a Jiak99 database"):
        restore.restore(other, url, MOMENT, port=port)
    with pytest.raises(restore.RestoreError, match="SQLite database only"):
        restore.restore(other, "postgresql+psycopg://x@127.0.0.1/jiak", MOMENT, port=port)


def test_a_backup_from_a_newer_version_leaves_the_database_as_it_was(
    url: str, laptop: Database, tmp_path: Path
) -> None:
    book(laptop, "Keep me")
    made = backup.backup_now(laptop, tmp_path / "backups", MOMENT)
    assert made is not None
    laptop.engine.dispose()
    newer = sqlite3.connect(made)
    newer.execute("UPDATE alembic_version SET version_num = 'from_the_future'")
    newer.commit()
    newer.close()
    with pytest.raises(restore.RestoreError, match="made by a newer version of the app"):
        restore.restore(made, url, MOMENT, port=free_port())
    assert count_bookings(url) == 1  # nothing was touched


def test_the_restore_command(
    url: str,
    laptop: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    made = backup.backup_now(laptop, tmp_path / "backups", MOMENT)
    assert made is not None
    laptop.engine.dispose()
    monkeypatch.setenv("DATABASE_URL", url)
    port = str(free_port())
    assert restore.main([str(made), "--port", port]) == 0
    assert "Outlook sync is paused" in capsys.readouterr().out
    assert restore.main([str(tmp_path / "missing.db"), "--port", port]) == 2
    assert "restore: There is no file" in capsys.readouterr().err


# --- paused Outlook sync and the after-restore list ---------------------------------------------


def pause(database: Database) -> None:
    with database.make_session() as db:
        state = db.get(models.OutlookState, 1)
        assert state is not None
        state.sync_paused = True
        state.restored_from = "jiak99-2026-11-11-1400.db"
        db.commit()


def test_while_paused_nothing_goes_to_or_comes_from_outlook(laptop: Database) -> None:
    calendar = FakeGateway()
    sync_dates(laptop, calendar, [])  # creates the state row
    with laptop.make_session() as db:
        db.add(models.OutlookState(id=1, last_error=""))
        db.commit()
    pause(laptop)
    book(laptop, "Ong party")
    assert sync_dates(laptop, calendar, [DATE])
    typed = calendar.create_event(
        CalendarEvent(id="", date=DATE, start=600, end=660, subject="[CHE] 12:00 · 6A · Lim")
    )
    result = sweep(laptop, calendar, NOW)
    assert (result.booked, calendar.events) == ([], {typed.id: typed})


def test_the_after_restore_list_and_resuming(laptop: Database) -> None:
    calendar = FakeGateway()
    same = book(laptop, "Unchanged")
    gone = book(laptop, "Event gone", start=660)
    moved = book(laptop, "Moved", start=720)
    assert sync_dates(laptop, calendar, [DATE])
    with laptop.make_session() as db:
        ids = {b.id: b.outlook_event_id for b in db.scalars(select(models.Booking))}
    calendar.delete_event(ids[gone] or "")
    moved_event = calendar.events[ids[moved] or ""]
    calendar.events[moved_event.id] = replace(moved_event, start=780, end=840)
    stranger = calendar.create_event(
        CalendarEvent(
            id="",
            date=DATE,
            start=900,
            end=960,
            subject="CHE · Booked later · 4 pax · 15:00–16:00",
            body="Booking: lost   Ticket: Classic",
            booking_id="lost-booking",
        )
    )
    typed = calendar.create_event(
        CalendarEvent(
            id="", date="2026-12-02", start=600, end=660, subject="[CHE] 10:00 · 6A · Lim"
        )
    )
    pause(laptop)
    client = TestClient(create_app(laptop, clock=lambda: NOW, gateway=calendar))

    banner = client.get("/outlook-status").text
    assert "Restored from backup jiak99-2026-11-11-1400.db" in banner
    assert '<a href="/restore-check">' in banner

    listing = client.get("/restore-check").text
    assert "1. Booked after the backup (1)" in listing
    assert "CHE · Booked later · 4 pax" in listing
    assert "2. Event gone from Outlook (1)" in listing
    assert f'<a href="/bookings/{gone}">Event gone</a>' in listing
    assert "3. Changed in Outlook (1)" in listing
    assert "time: Outlook 13:00, app 12:00" in listing
    assert "4. Typed in Outlook (1)" in listing
    assert f'href="/bookings/{same}"' not in listing  # unchanged: not listed

    response = client.post("/restore-check/resume", follow_redirects=False)
    assert response.status_code == 303
    assert "Outlook sync is on" in client.get("/restore-check").text
    assert client.get("/outlook-status").text == ""

    # The app is the truth again: the gone event is made again, the moved one put back.
    with laptop.make_session() as db:
        events_by_booking = {
            e.booking_id: e for e in calendar.events.values() if e.booking_id is not None
        }
        assert events_by_booking[gone].start == 660
        assert events_by_booking[moved].start == 720
    # Never deleted: the stranger gets a note and the category, its old text kept.
    marked = calendar.events[stranger.id]
    assert marked.categories == ("Not in app",)
    assert marked.body.startswith(NOT_IN_APP_NOTE)
    assert marked.body.endswith("Booking: lost   Ticket: Classic")
    # The sweep ran: the typed entry is booked.
    assert calendar.events[typed.id].booking_id is not None


def test_the_after_restore_page_when_nothing_was_restored(laptop: Database) -> None:
    client = TestClient(create_app(laptop, clock=lambda: NOW))
    assert "Outlook sync is on. Nothing to check here." in client.get("/restore-check").text


def test_the_backup_command(
    url: str,
    laptop: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.delenv("JIAK_BACKUP_DIR", raising=False)
    assert backup.main([]) == 2
    assert "set JIAK_BACKUP_DIR" in capsys.readouterr().err

    folder = tmp_path / "backups"
    monkeypatch.setenv("JIAK_BACKUP_DIR", str(folder))
    assert backup.main([]) == 0
    assert "Backup made: " in capsys.readouterr().out
    assert len(list(folder.glob("jiak99-*.db"))) == 1

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x@127.0.0.1/jiak")
    assert backup.main(["--to", str(folder)]) == 2
    assert "SQLite" in capsys.readouterr().err

    monkeypatch.setenv("OneDrive", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", url)
    assert backup.main([]) == 2
    assert "inside OneDrive" in capsys.readouterr().err
