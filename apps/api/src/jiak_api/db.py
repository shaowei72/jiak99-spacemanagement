"""Database connection and the per-date booking lock (SPEC §5.1, §8)."""

import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker


class Database:
    """One database: the SQLAlchemy engine plus a factory for sessions (units of work)."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.is_sqlite = url.startswith("sqlite")
        connect_args = {"check_same_thread": False} if self.is_sqlite else {}
        self.engine: Engine = create_engine(url, connect_args=connect_args)
        self.make_session = sessionmaker(self.engine, expire_on_commit=False)
        # SQLite only (local dev): one lock per date, shared by every thread in this process.
        self._sqlite_locks: dict[str, threading.Lock] = {}
        self._sqlite_locks_guard = threading.Lock()

    @contextmanager
    def date_lock(self, db: Session, date: str) -> Iterator[None]:
        """Only one booking change per date at a time. Commit inside the `with` block.

        PostgreSQL: a transaction-level advisory lock, released when the transaction ends, so it
        works across several API processes. SQLite: an in-process lock, fine for one dev server.
        """
        if not self.is_sqlite:
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": date_lock_key(date)})
            yield
            return
        with self._sqlite_locks_guard:
            lock = self._sqlite_locks.setdefault(date, threading.Lock())
        with lock:
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


def date_lock_key(date: str) -> int:
    """'2026-12-01' -> 20261201: a unique lock number per date."""
    return int(date.replace("-", ""))
