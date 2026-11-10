"""The Alembic migrations build exactly the tables that models.py describes, and can be undone."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from jiak_api.migrate import alembic_config, downgrade_to_empty, upgrade_to_latest
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


def test_route_migration_unlocks_locked_days(database_url: str) -> None:
    """Sessions lose their zone in the zone-routes migration, so a locked day must not stay
    locked: its groups would be "locked" with no route to keep."""
    command.upgrade(alembic_config(database_url), "9d6f663453df")  # before zone routes
    engine = create_engine(database_url)
    now = "2026-11-09 08:00:00+00:00"
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO customers (id, name) VALUES ('c1', 'Tan family')"))
        connection.execute(
            text(
                "INSERT INTO bookings (id, customer_id, date, start, ticket_type, adults, "
                "children, status, notes, created_by, created_at, updated_at) VALUES ('b1', "
                "'c1', '2026-12-01', 600, 'PREMIUM', 10, 0, 'confirmed', '', 'test', :at, :at)"
            ),
            {"at": now},
        )
        connection.execute(
            text(
                "INSERT INTO sessions (id, booking_id, date, start, ticket_type, adults, "
                "children, zone_id, pinned, status) VALUES ('s1', 'b1', '2026-12-01', 600, "
                "'PREMIUM', 10, 0, 'A', :yes, 'locked')"
            ),
            {"yes": True},
        )
        connection.execute(
            text(
                "INSERT INTO day_settings (date, closed, blackouts, locked_at) "
                "VALUES ('2026-12-01', :no, CAST(:none AS JSON), :at)"
            ),
            {"no": False, "none": "[]", "at": now},
        )

    upgrade_to_latest(database_url)
    with engine.connect() as connection:
        session = connection.execute(text("SELECT route, pinned, status FROM sessions")).one()
        locked_at = connection.execute(text("SELECT locked_at FROM day_settings")).scalar_one()
    engine.dispose()
    assert tuple(session) == (None, False, "open")
    assert locked_at is None
