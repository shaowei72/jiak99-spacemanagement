"""Shared test setup: a fresh database per test and a fixed "now".

By default each test gets its own SQLite file. To run the same tests against PostgreSQL (needed
for the concurrency test to count, SPEC §8), set TEST_DATABASE_URL, e.g.
    postgresql+psycopg://jiak:jiak@localhost:5432/jiak_test
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jiak_api.clock import Now
from jiak_api.db import Database
from jiak_api.main import create_app
from jiak_api.models import Base

TODAY = Now(today="2026-11-07", minute=8 * 60)  # 08:00, before opening


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    database = Database(url)
    Base.metadata.drop_all(database.engine)
    Base.metadata.create_all(database.engine)
    yield database
    database.engine.dispose()


@pytest.fixture
def now() -> Now:
    return TODAY


@pytest.fixture
def client(database: Database) -> TestClient:
    return TestClient(create_app(database, clock=lambda: TODAY))
