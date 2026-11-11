"""Put a backup back after the laptop was lost or broken (SPEC §5.6).

    uv run restore "C:\\Users\\<name>\\OneDrive - Jiak99\\Jiak99 backups\\jiak99-2026-11-11-1400.db"

Stop the app first. The command:

1. checks the backup is a sound Jiak99 database, made by this version of the app or an older one;
2. sets the current database aside (renamed 'jiak99-replaced-<date>-<time>.db', never deleted);
3. puts the backup in its place and brings it up to this version of the app;
4. pauses Outlook sync: Outlook is newer than the backup, so the normal sync would bring back events
   of bookings cancelled after the backup and undo later changes. Start the app and open the
   after-restore list (http://127.0.0.1:8000/restore-check): it shows what differs, you fix it in
   the app, then press "Resume Outlook sync".

Uses DATABASE_URL like the app does (SQLite only).
"""

import argparse
import shutil
import socket
import sqlite3
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from alembic.script import ScriptDirectory

from jiak_api import models
from jiak_api.backup import BackupError, check_copy
from jiak_api.clock import SINGAPORE
from jiak_api.config import load_config
from jiak_api.db import Database, DatabaseLocationError, sqlite_file
from jiak_api.migrate import alembic_config, upgrade_to_latest
from jiak_api.outlook_sync import outlook_state

APP_PORT = 8000


class RestoreError(Exception):
    """The restore can't go on; the message says why and what to do."""


def app_is_running(port: int = APP_PORT) -> bool:
    """True if something answers on this laptop's app port (the app is still running)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def backup_version(backup: Path) -> str:
    """The database version (Alembic revision) a backup was made at."""
    connection = sqlite3.connect(backup)
    try:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        connection.close()
    return str(row[0]) if row else ""


def check_version_known(backup: Path, database_url: str) -> None:
    """Refuse a backup made by a newer version of the app (a version this app doesn't know):
    it can't be brought up to date here. Update the app first."""
    version = backup_version(backup)
    scripts = ScriptDirectory.from_config(alembic_config(database_url))
    try:
        known = scripts.get_revision(version) is not None
    except Exception:
        known = False
    if not known:
        raise RestoreError(
            f"{backup.name} was made by a newer version of the app (database version {version!r}). "
            "Update the app first (docs/LAPTOP-INSTALL.md, D2), then restore again."
        )


def set_aside(database: Path, now: datetime) -> Path | None:
    """Rename the current database (and its WAL files) to 'jiak99-replaced-<date>-<time>.db'.
    Returns the new name, or None if there was no database."""
    if not database.exists():
        return None
    stamp = now.astimezone(SINGAPORE).strftime("%Y-%m-%d-%H%M%S")
    aside = database.with_name(f"{database.stem}-replaced-{stamp}{database.suffix}")
    database.rename(aside)
    for extra in ("-wal", "-shm"):  # SQLite's WAL mode files go with the database
        companion = database.with_name(database.name + extra)
        if companion.exists():
            companion.rename(aside.with_name(aside.name + extra))
    return aside


def pause_outlook_sync(database_url: str, backup_name: str) -> None:
    """Mark the restored database: Outlook sync paused until the owner resumes it."""
    database = Database(database_url)
    try:
        with database.make_session() as db:
            state = outlook_state(db)
            state.sync_paused = True
            state.restored_from = backup_name[:300]
            state.restored_at = models.utc_now()
            db.commit()
    finally:
        database.engine.dispose()


def restore(backup: Path, database_url: str, now: datetime, port: int = APP_PORT) -> list[str]:
    """Do the restore (module docstring) and return lines to print."""
    target = sqlite_file(database_url)
    if target is None:
        raise RestoreError("restore works on the laptop's SQLite database only (DATABASE_URL).")
    if app_is_running(port):
        raise RestoreError(
            f"The app is still running (something answers on port {port}). Stop it first."
        )
    if not backup.is_file():
        raise RestoreError(f"There is no file {backup}.")
    try:
        check_copy(backup)
    except BackupError as error:
        raise RestoreError(str(error)) from error
    check_version_known(backup, database_url)

    target.parent.mkdir(parents=True, exist_ok=True)
    aside = set_aside(target, now)
    shutil.copyfile(backup, target)
    upgrade_to_latest(database_url)
    pause_outlook_sync(database_url, backup.name)

    lines = [f"Restored {backup.name} into {target}."]
    if aside is not None:
        lines.append(f"The database it replaced was kept as {aside.name}.")
    lines += [
        "Outlook sync is paused. Start the app, open http://127.0.0.1:8000/restore-check,",
        'fix what it lists, then press "Resume Outlook sync".',
    ]
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="restore", description="Put a backup back (stop the app first)."
    )
    parser.add_argument("backup", help="the backup file, e.g. jiak99-2026-11-11-1400.db")
    parser.add_argument("--port", type=int, default=APP_PORT, help="the app's port (default 8000)")
    args = parser.parse_args(argv)
    try:
        lines = restore(
            Path(args.backup), load_config().database_url, models.utc_now(), port=args.port
        )
    except (RestoreError, DatabaseLocationError) as error:
        print(f"restore: {error}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
