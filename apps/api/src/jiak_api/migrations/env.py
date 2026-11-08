"""Alembic runs this file for every migration command. It connects to the database and runs.

Which database: the URL given by `jiak_api.migrate` (tests), otherwise the DATABASE_URL
environment variable (the same one the app uses), otherwise the local SQLite dev file.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from jiak_api.config import load_config
from jiak_api.models import Base

config = context.config

# Only the command line (`uv run alembic ...`) has an alembic.ini with logging settings.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = config.get_main_option("sqlalchemy.url") or load_config().database_url


def run_migrations() -> None:
    """Connect to the database and apply the pending migrations in one go."""
    engine = create_engine(database_url)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            # What autogenerate compares the database against: our models.
            target_metadata=Base.metadata,
            # Notice column type changes too (e.g. String(20) -> String(50)).
            compare_type=True,
            # SQLite can't change most columns in place; "batch mode" rebuilds the table instead.
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations()
