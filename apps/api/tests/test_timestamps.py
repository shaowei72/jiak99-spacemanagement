"""Timestamps always carry a timezone: refused without one, stored and read back in UTC."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import StatementError

from jiak_api.db import Database
from jiak_api.models import AuditLog

SINGAPORE = timezone(timedelta(hours=8))


def save_audit_row(database: Database, at: datetime) -> int:
    with database.make_session() as db:
        row = AuditLog(actor="test", action="test", entity="test", entity_id="x", at=at)
        db.add(row)
        db.commit()
        return row.id


def test_a_time_comes_back_as_the_same_moment_in_utc(database: Database) -> None:
    ten_am_singapore = datetime(2026, 12, 1, 10, 0, tzinfo=SINGAPORE)
    row_id = save_audit_row(database, ten_am_singapore)

    # A new session, so the value really comes from the database.
    with database.make_session() as db:
        at = db.get_one(AuditLog, row_id).at
    assert at == ten_am_singapore  # the same moment...
    assert at.tzinfo == UTC  # ...labelled as UTC
    assert at.hour == 2  # 10:00 in Singapore is 02:00 UTC


def test_a_time_without_a_timezone_is_refused(database: Database) -> None:
    with pytest.raises(StatementError, match="has no timezone"):
        save_audit_row(database, datetime(2026, 12, 1, 10, 0))


def test_new_rows_get_a_utc_timestamp_by_default(database: Database) -> None:
    with database.make_session() as db:
        row = AuditLog(actor="test", action="test", entity="test", entity_id="x")
        db.add(row)
        db.commit()
        row_id = row.id
    with database.make_session() as db:
        at = db.get_one(AuditLog, row_id).at
    assert at.tzinfo == UTC
