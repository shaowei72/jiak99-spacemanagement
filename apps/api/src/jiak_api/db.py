"""Database connection and the per-date booking lock (SPEC §5.1, §5.5, §8).

PostgreSQL (Docker, later the cloud) and SQLite (the owner's laptop, SPEC §5.5) both work. On SQLite
the app switches on the safeguards of SPEC §5.5:
- WAL mode and a 30-second wait when the file is busy, so the web pages, the Outlook sync and the
  sweep can work at the same time;
- every booking change takes SQLite's write lock at its start (`BEGIN IMMEDIATE`), so a second
  program on the same file (e.g. `uv run import-outlook` while the app runs) can't double-book;
- the database file must not be in a folder OneDrive syncs (`check_database_location`).
"""

import os
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, create_engine, event, make_url, text
from sqlalchemy.orm import Session, sessionmaker

BUSY_WAIT_MS = 30_000  # how long SQLite waits for another writer before giving up

# Environment variables Windows sets to the OneDrive folders of the signed-in user.
ONEDRIVE_VARIABLES = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")


class DatabaseLocationError(Exception):
    """The SQLite file is somewhere it could be damaged (a folder OneDrive syncs)."""


def sqlite_file(url: str) -> Path | None:
    """The SQLite file a database URL points at, or None (not SQLite, or in memory)."""
    parsed = make_url(url)
    if not parsed.drivername.startswith("sqlite") or not parsed.database:
        return None
    if parsed.database == ":memory:":
        return None
    return Path(parsed.database)


def check_database_location(url: str, environ: Mapping[str, str] = os.environ) -> None:
    """Refuse a SQLite file inside a OneDrive folder (SPEC §5.5): syncing a database file while the
    app writes to it can damage it. Backups go to OneDrive; the live file never does."""
    path = sqlite_file(url)
    if path is None:
        return
    where = path.resolve()
    for variable in ONEDRIVE_VARIABLES:
        folder = environ.get(variable)
        if folder and where.is_relative_to(Path(folder).resolve()):
            raise DatabaseLocationError(
                f"The database {where} is inside OneDrive ({folder}). OneDrive can damage a "
                "database it syncs while the app runs. Put it in a local folder, e.g. "
                r"%LOCALAPPDATA%\Jiak99\jiak99.db (docs/LAPTOP-INSTALL.md, step B4)."
            )


def _sqlite_settings(dbapi_connection: Any, _record: Any) -> None:
    """Run for every new SQLite connection: the busy wait, and WAL mode.

    WAL mode stays with the file once set, so it is only switched on when the file isn't in it
    yet. Switching doesn't wait for other connections, so if several connect to a new file at
    once and one of them switches first, the others just check again.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute(f"PRAGMA busy_timeout={BUSY_WAIT_MS}")
    if cursor.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            if cursor.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                raise
    cursor.close()


class Database:
    """One database: the SQLAlchemy engine plus a factory for sessions (units of work)."""

    def __init__(self, url: str) -> None:
        check_database_location(url)
        self.url = url
        self.is_sqlite = url.startswith("sqlite")
        connect_args: dict[str, Any] = {}
        if self.is_sqlite:
            connect_args = {"check_same_thread": False, "timeout": BUSY_WAIT_MS / 1000}
        self.engine: Engine = create_engine(url, connect_args=connect_args)
        if self.is_sqlite:
            event.listen(self.engine, "connect", _sqlite_settings)
        self.make_session = sessionmaker(self.engine, expire_on_commit=False)
        # SQLite: one lock per date, shared by every thread in this process (the write lock
        # below covers other programs).
        self._sqlite_locks: dict[str, threading.Lock] = {}
        self._sqlite_locks_guard = threading.Lock()

    @contextmanager
    def date_lock(self, db: Session, date: str) -> Iterator[None]:
        """Only one booking change per date at a time. Commit inside the `with` block.

        PostgreSQL: a transaction-level advisory lock, released when the transaction ends, so it
        works across several API processes. SQLite: an in-process lock per date, plus SQLite's
        own write lock taken at once, which other programs on the same file wait for.
        """
        if not self.is_sqlite:
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": date_lock_key(date)})
            yield
            return
        with self._sqlite_locks_guard:
            lock = self._sqlite_locks.setdefault(date, threading.Lock())
        with lock:
            take_sqlite_write_lock(db)
            yield

    @contextmanager
    def date_locks(self, db: Session, dates: list[str]) -> Iterator[None]:
        """Lock several dates at once, e.g. the old and new date when a booking moves.

        Dates are locked in calendar order, and each only once. If two requests both need
        2026-12-01 and 2026-12-02, they queue on 12-01 first, so they can never deadlock.
        """
        with ExitStack() as stack:
            for date in sorted(set(dates)):
                stack.enter_context(self.date_lock(db, date))
            yield


def take_sqlite_write_lock(db: Session) -> None:
    """Start the session's transaction with SQLite's write lock (`BEGIN IMMEDIATE`), unless it
    already writes. Everything read after this sees the latest data, and no other connection
    can write until this transaction ends (it waits up to BUSY_WAIT_MS)."""
    connection = db.connection()
    raw = cast(sqlite3.Connection, connection.connection.dbapi_connection)
    if not raw.in_transaction:  # no write yet: Python's sqlite3 begins one only before a write
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def date_lock_key(date: str) -> int:
    """'2026-12-01' -> 20261201: a unique lock number per date."""
    return int(date.replace("-", ""))
