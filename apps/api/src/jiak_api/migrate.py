"""Bring a database's tables up to date by running the Alembic migrations.

The command-line way (from the repo root) is `uv run alembic upgrade head`. This module does the
same from Python, for the tests and (later) for the Docker start-up.
"""

from alembic import command
from alembic.config import Config

from jiak_api.db import Database


def alembic_config(database_url: str) -> Config:
    """Alembic settings pointing at `database_url`, without needing alembic.ini."""
    config = Config()
    config.set_main_option("script_location", "jiak_api:migrations")
    # Alembic treats "%" as special in option values, so a "%" in a password must be doubled.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade_to_latest(database_url: str) -> None:
    """Apply every migration the database has not had yet ("upgrade head"). A SQLite file is
    then switched to WAL mode once (see jiak_api.db), before the app's connections open it."""
    command.upgrade(alembic_config(database_url), "head")
    if database_url.startswith("sqlite"):
        database = Database(database_url)
        with database.engine.connect():
            pass  # connecting applies the SQLite settings
        database.engine.dispose()


def downgrade_to_empty(database_url: str) -> None:
    """Undo every migration, leaving no app tables ("downgrade base")."""
    command.downgrade(alembic_config(database_url), "base")
