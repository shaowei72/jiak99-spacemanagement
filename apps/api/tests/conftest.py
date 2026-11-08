"""Shared test setup: a fresh database per test and a fixed "now".

By default each test gets its own SQLite file. To run the same tests against PostgreSQL (needed
for the concurrency test to count, SPEC §8), set TEST_DATABASE_URL, e.g.
    postgresql+psycopg://jiak:jiak@127.0.0.1:5432/jiak_test
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from jiak_api.clock import Now
from jiak_api.db import Database
from jiak_api.main import create_app
from jiak_api.migrate import upgrade_to_latest
from jiak_api.models import Base

TODAY = Now(today="2026-11-07", minute=8 * 60)  # 08:00, before opening


@pytest.fixture
def database_url(tmp_path: Path) -> Iterator[str]:
    """An empty database: a new SQLite file, or the PostgreSQL one with every table dropped."""
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    if not url.startswith("sqlite"):
        engine = create_engine(url)
        Base.metadata.drop_all(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        engine.dispose()
    yield url


@pytest.fixture
def database(database_url: str) -> Iterator[Database]:
    """A database built by the Alembic migrations, exactly as production will be."""
    upgrade_to_latest(database_url)
    database = Database(database_url)
    yield database
    database.engine.dispose()


@pytest.fixture
def now() -> Now:
    return TODAY


@pytest.fixture
def client(database: Database) -> TestClient:
    return TestClient(create_app(database, clock=lambda: TODAY))
