"""No double-booking under concurrent requests (SPEC §8).

Many sales staff try to book the same slot at the same moment. Only 4 zones exist, so exactly
4 bookings may succeed. Runs on SQLite by default; set TEST_DATABASE_URL to run on PostgreSQL.
"""

from concurrent.futures import ThreadPoolExecutor

from engine import Enquiry
from jiak_api import bookings
from jiak_api.clock import Now
from jiak_api.db import Database


def test_only_four_of_ten_simultaneous_bookings_succeed(database: Database, now: Now) -> None:
    enquiry = Enquiry(date="2026-12-01", start=10 * 60, ticket_type="PREMIUM", adults=5)

    def try_to_book(number: int) -> bool:
        with database.make_session() as db:
            try:
                bookings.create_booking(
                    database,
                    db,
                    enquiry,
                    bookings.CustomerDetails(name=f"Group {number}"),
                    notes="",
                    actor=f"sales{number}",
                    now=now,
                )
            except bookings.NoLongerFits:
                return False
            return True

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(try_to_book, range(10)))

    assert results.count(True) == 4
    assert results.count(False) == 6
