"""The Alembic migrations build exactly the tables that models.py describes, and can be undone."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from jiak_api.migrate import downgrade_to_empty, upgrade_to_latest
from jiak_api.models import Base

APP_TABLES = {"app_settings", "audit_log", "bookings", "customers", "day_settings", "sessions"}


def test_migrations_match_the_models(database_url: str) -> None:
    """If this fails, models.py changed without a migration: run
    `uv run alembic revision --autogenerate -m "what changed"` and check the new file."""
    upgrade_to_latest(database_url)
    engine = create_engine(database_url)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = compare_metadata(context, Base.metadata)
    engine.dispose()
    assert differences == []


def test_downgrade_removes_every_table_and_upgrade_rebuilds_them(database_url: str) -> None:
    upgrade_to_latest(database_url)
    downgrade_to_empty(database_url)
    engine = create_engine(database_url)
    assert set(inspect(engine).get_table_names()) & APP_TABLES == set()
    upgrade_to_latest(database_url)
    assert set(inspect(engine).get_table_names()) >= APP_TABLES
    engine.dispose()
