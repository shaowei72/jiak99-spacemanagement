"""Hourly backups of the laptop's SQLite database (SPEC §5.6).

While the app runs it copies the database every hour (and when it starts, if the newest copy is over
an hour old) into the folder in JIAK_BACKUP_DIR, normally in the owner's OneDrive for Business, which
takes the copies off the laptop. Each copy:

- is made with SQLite's own backup function, which gives a complete, consistent copy even while the
  app is writing;
- is written under a temporary name and renamed when finished, so OneDrive never picks up half a file;
- is checked (`PRAGMA integrity_check`) before it counts.

Kept: every copy from the last 2 days, and the newest copy of each day for 30 days. Older ones are
deleted. The result of every backup is recorded; a failure (or no backup for 2 hours) shows in the
warning banner. PostgreSQL isn't backed up this way (the cloud database has its own backups).
"""

import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from jiak_api import models
from jiak_api.clock import SINGAPORE
from jiak_api.db import Database, sqlite_file

log = logging.getLogger(__name__)

EVERY = timedelta(hours=1)
KEEP_ALL_FOR = timedelta(days=2)
KEEP_DAILY_FOR = timedelta(days=30)
NAME_PREFIX = "jiak99-"
_NAME = re.compile(r"^jiak99-(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})\.db$")


class BackupError(Exception):
    """A backup couldn't be made; the message says why."""


@dataclass(frozen=True)
class BackupFile:
    path: Path
    made: datetime  # when, as written in its name (Singapore time)


def backup_name(moment: datetime) -> str:
    """'jiak99-2026-11-11-1430.db' for 14:30 Singapore time."""
    return moment.astimezone(SINGAPORE).strftime(f"{NAME_PREFIX}%Y-%m-%d-%H%M.db")


def read_backup_name(name: str) -> datetime | None:
    """The time in a backup's file name, or None if it isn't one of ours."""
    match = _NAME.match(name)
    if match is None:
        return None
    year, month, day, hour, minute = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, hour, minute, tzinfo=SINGAPORE)
    except ValueError:
        return None


def list_backups(folder: Path) -> list[BackupFile]:
    """Our backups in `folder`, oldest first."""
    if not folder.is_dir():
        return []
    found: list[BackupFile] = []
    for path in folder.iterdir():
        made = read_backup_name(path.name)
        if made is not None and path.is_file():
            found.append(BackupFile(path=path, made=made))
    found.sort(key=lambda backup: backup.made)
    return found


def backups_to_delete(backups: list[BackupFile], now: datetime) -> list[BackupFile]:
    """Which backups the keep rule lets go: everything from the last 2 days stays; before that,
    the newest of each day stays for 30 days; anything older goes."""
    newest_of_day: dict[str, BackupFile] = {}
    for backup in backups:  # oldest first, so the last one seen per day is the newest
        newest_of_day[backup.made.strftime("%Y-%m-%d")] = backup
    keep_daily = {id(backup) for backup in newest_of_day.values()}

    delete: list[BackupFile] = []
    for backup in backups:
        age = now - backup.made
        if age <= KEEP_ALL_FOR:
            continue
        if age <= KEEP_DAILY_FOR and id(backup) in keep_daily:
            continue
        delete.append(backup)
    return delete


def check_copy(path: Path) -> None:
    """Raise BackupError unless `path` is a sound copy of the app's database."""
    connection = sqlite3.connect(path)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise BackupError(f"{path.name} failed its check: {result}")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    finally:
        connection.close()
    if not {"alembic_version", "bookings"} <= tables:
        raise BackupError(f"{path.name} is not a Jiak99 database")


def make_backup(database_url: str, folder: Path, now: datetime) -> Path:
    """Copy the database into `folder` as 'jiak99-<date>-<time>.db' and return its path."""
    source_path = sqlite_file(database_url)
    if source_path is None:
        raise BackupError("Backups need the SQLite database of the laptop (SPEC §5.6)")
    if not source_path.exists():
        raise BackupError(f"The database {source_path} doesn't exist")
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / backup_name(now)
    partial = folder / (final.name + ".partial")
    partial.unlink(missing_ok=True)

    source = sqlite3.connect(source_path)
    target = sqlite3.connect(partial)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    check_copy(partial)
    partial.replace(final)  # only now does the finished copy appear under its own name
    return final


# --- recording, for the warning banner -------------------------------------------------------


def backup_state(database: Database) -> models.BackupState:
    """The one row describing the backups (made on first use)."""
    with database.make_session() as db:
        state = db.get(models.BackupState, 1)
        if state is None:
            state = models.BackupState(id=1, last_file="", last_error="")
            db.add(state)
            db.commit()
        return state


def record_backup(database: Database, *, file: str = "", error: str = "") -> None:
    """Note that a backup worked (`file`) or failed (`error`)."""
    backup_state(database)
    with database.make_session() as db:
        state = db.get(models.BackupState, 1)
        assert state is not None  # made just above
        moment = models.utc_now()
        if error:
            state.last_error = error[:500]
            state.last_error_at = moment
        else:
            state.last_ok = moment
            state.last_file = file
        db.commit()


def backup_now(database: Database, folder: Path, now: datetime) -> Path | None:
    """Make a backup, delete the ones the keep rule lets go, and record the result. Never raises:
    a failure is logged and recorded (it shows in the banner)."""
    try:
        made = make_backup(database.url, folder, now)
        for old in backups_to_delete(list_backups(folder), now):
            old.path.unlink(missing_ok=True)
    except Exception as error:
        log.exception("Backup failed")
        record_backup(database, error=f"Backup failed: {error}")
        return None
    record_backup(database, file=made.name)
    log.info("Backup made: %s", made)
    return made


def backup_due(folder: Path, now: datetime) -> bool:
    """True if the newest backup is an hour old or more (or there is none)."""
    backups = list_backups(folder)
    if not backups:
        return True
    return now - backups[-1].made >= EVERY
