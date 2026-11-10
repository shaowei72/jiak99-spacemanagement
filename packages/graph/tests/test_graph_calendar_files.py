"""The take-over reader against whole calendar files (SPEC §5.4 "Test data").

- `fixtures/operations-anon.ics`: the anonymised copy of the operations@ calendar as saved on
  2026-11-10, made by `uv run anonymise-ics` and read by the owner before it was committed.
- The real file: only when the environment variable JIAK_REAL_CALENDAR_ICS points at it (never in
  the repo). These tests check that nothing in it breaks the reader.
"""

import os
from pathlib import Path

import pytest

from graph.ics import parse_ics
from graph.takeover import (
    ACTION_BOOK,
    ACTION_FILL_IN,
    ACTION_HISTORY,
    ACTION_OVER_100,
    ACTION_SKIP,
    ReviewRow,
    build_review,
    count_actions,
)

FIXTURE = Path(__file__).parent / "fixtures" / "operations-anon.ics"
REAL_FILE = os.environ.get("JIAK_REAL_CALENDAR_ICS", "")
TAKE_OVER_DATE = "2026-11-10"


@pytest.mark.skipif(not FIXTURE.exists(), reason="anonymised calendar not committed yet")
def test_anonymised_calendar() -> None:
    events = parse_ics(FIXTURE.read_text(encoding="utf-8"))
    review = build_review(events, TAKE_OVER_DATE, [])

    assert len(events) == 88
    assert len(review.others) == 8  # meetings, site visits, a race weekend
    assert count_actions(review.rows) == {
        ACTION_BOOK: 24,
        ACTION_FILL_IN: 29,
        ACTION_OVER_100: 1,
        ACTION_SKIP: 2,
        ACTION_HISTORY: 24,
    }
    future = [row for row in review.rows if row.when == "future" and not row.cancelled]
    assert len(future) == 54
    assert sum(1 for row in future if row.status == "confirmed") == 23
    assert sum(1 for row in future if row.soft_block) == 29  # soft blocks are taken over too
    assert sum(1 for row in future if row.status == "") == 2  # no tag at all
    (big,) = [row for row in review.rows if row.action == ACTION_OVER_100]
    assert (big.date, big.people) == ("2026-12-04", 110)


def _key(rows: tuple[ReviewRow, ...]) -> list[tuple[str, str, str, int | None, str, str]]:
    """What must not change when a calendar is anonymised."""
    return [(r.date, r.start, r.action, r.people, r.ticket, r.status) for r in rows]


@pytest.mark.skipif(not REAL_FILE, reason="set JIAK_REAL_CALENDAR_ICS to the real calendar file")
def test_real_calendar_reads_like_the_anonymised_one() -> None:
    real = build_review(parse_ics(Path(REAL_FILE).read_text(encoding="utf-8")), TAKE_OVER_DATE, [])
    assert real.rows, "no booking-like events found"
    if FIXTURE.exists():
        anonymised = build_review(
            parse_ics(FIXTURE.read_text(encoding="utf-8")), TAKE_OVER_DATE, []
        )
        assert _key(real.rows) == _key(anonymised.rows)
