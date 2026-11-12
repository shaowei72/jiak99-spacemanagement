"""Carrying corrections over from an earlier review sheet to a fresh one (Milestone 5)."""

import pytest

from graph.carryover import CarryOverError, carry_over, carry_row, read_earlier_sheet
from graph.takeover import (
    ACTION_BOOK,
    ACTION_CHECK,
    ACTION_FILL_IN,
    ACTION_HISTORY,
    ACTION_SKIP,
    ReviewRow,
    review_csv,
)


def fresh(**changes: object) -> ReviewRow:
    """A fresh-export row: by default a future 'fill in' row with no headcount or ticket."""
    cells: dict[str, object] = {
        "event_id": "uid-1",
        "date": "2026-12-01",
        "start": "10:00",
        "end": "12:00",
        "people": None,
        "adults": None,
        "children": None,
        "ticket": "",
        "client": "Kingfisher Travel",
        "status": "soft block",
        "soft_block": True,
        "cancelled": False,
        "when": "future",
        "action": ACTION_FILL_IN,
        "notes": "no headcount found; no ticket type found",
        "people_found": "",
        "ticket_found": "",
        "subject": "[Soft Block] Kingfisher Travel",
    }
    cells.update(changes)
    return ReviewRow(**cells)  # type: ignore[arg-type]


def earlier(row: ReviewRow, **cells: str) -> dict[str, str]:
    """The cells of `row` as the earlier sheet has them, with the owner's edits in `cells`."""
    sheet = read_earlier_sheet(review_csv([row]))
    found = sheet[row.event_id]
    found.update(cells)
    return found


def test_filled_in_cells_are_carried_over() -> None:
    row = fresh()
    new, took, differs = carry_row(row, earlier(row, people="20", adults="", ticket="phe"))
    assert (took, differs) == (True, False)
    assert (new.people, new.adults, new.ticket) == (20, None, "PREMIUM")
    assert new.action == ACTION_BOOK
    assert new.notes == (
        "corrections carried over from the earlier sheet; no headcount found; no ticket type found"
    )


def test_a_row_nobody_changed_stays_as_it_is() -> None:
    row = fresh(people=18, adults=18, children=0, ticket="PREMIUM", action=ACTION_BOOK)
    assert carry_row(row, earlier(row)) == (row, False, False)
    # The same ticket written as its short name is not a change.
    assert carry_row(row, earlier(row, ticket="PHE")) == (row, False, False)
    # A yes/no cell emptied on the earlier sheet is not a change either.
    assert carry_row(row, earlier(row, soft_block="")) == (row, False, False)


def test_owner_value_wins_but_a_disagreement_is_flagged() -> None:
    row = fresh(people=18, adults=18, children=0, ticket="PREMIUM", action=ACTION_BOOK, notes="")
    new, took, differs = carry_row(
        row, earlier(row, people="20", adults="20", client="Kingfisher", soft_block="FALSE")
    )
    assert (took, differs) == (True, True)
    assert (new.people, new.adults, new.client, new.soft_block) == (20, 20, "Kingfisher", False)
    assert new.action == ACTION_CHECK
    assert new.notes == (
        "kept from the earlier sheet: people 20 (Outlook now says 18), "
        "adults 20 (Outlook now says 18), client Kingfisher (Outlook now says Kingfisher Travel), "
        "soft_block FALSE (Outlook now says yes)"
    )


def test_a_ticket_correction_that_disagrees() -> None:
    row = fresh(people=10, adults=10, children=0, ticket="CLASSIC", action=ACTION_BOOK)
    new, _, differs = carry_row(row, earlier(row, ticket="PREMIUM_CT"))
    assert differs
    assert new.ticket == "PREMIUM_CT"
    assert "ticket PREMIUM_CT (Outlook now says CLASSIC)" in new.notes


def test_past_rows_stay_history_even_when_they_disagree() -> None:
    row = fresh(when="past", action=ACTION_HISTORY, ticket="CLASSIC")
    new, _, differs = carry_row(row, earlier(row, ticket="PREMIUM"))
    assert differs
    assert new.action == ACTION_HISTORY


def test_a_moved_event_keeps_its_new_time_and_says_where_it_was() -> None:
    row = fresh(date="2026-12-02", start="11:00", end="13:00")
    new, took, differs = carry_row(row, earlier(row, date="2026-12-01", start="10:00"))
    assert (took, differs) == (False, False)
    assert (new.date, new.start) == ("2026-12-02", "11:00")
    assert new.notes.startswith("moved in Outlook since the earlier sheet (was 2026-12-01 10:00)")
    # An event that was all-day on the earlier sheet:
    new, _, _ = carry_row(row, earlier(row, date="2026-12-01", start="", end=""))
    assert "(was 2026-12-01 all day)" in new.notes


def test_a_start_filled_in_for_an_all_day_event_is_carried_over() -> None:
    row = fresh(start="", end="", people=10, ticket="CLASSIC", action="check by hand")
    new, took, _ = carry_row(row, earlier(row, start="9:30:00", end="10:30"))
    assert took
    assert (new.start, new.end, new.action) == ("09:30", "10:30", ACTION_BOOK)
    new, _, _ = carry_row(row, earlier(row, start="09:30", end=""))
    assert (new.start, new.end) == ("09:30", "")


def test_cells_that_cant_be_read_are_not_carried_over() -> None:
    row = fresh(start="", end="")
    new, took, differs = carry_row(
        row,
        earlier(row, people="about 20", ticket="VIP", cancelled="maybe", start="lunch"),
    )
    assert (took, differs) == (False, False)
    assert new.people is None
    assert new.notes.split("; ")[:4] == [
        "earlier sheet's people 'about 20' is not a number: not carried over",
        "earlier sheet's ticket 'VIP' is not a ticket type: not carried over",
        "earlier sheet's cancelled 'maybe' is not yes or no: not carried over",
        "earlier sheet's start 'lunch' is not a time: not carried over",
    ]


def test_skip_written_by_the_owner_is_kept() -> None:
    row = fresh(people=10, ticket="CLASSIC", action=ACTION_BOOK, notes="")
    new, took, _ = carry_row(row, earlier(row, action="skip - duplicate of Tan"))
    assert took
    assert new.action == "skip - duplicate of Tan"
    assert new.notes == "corrections carried over from the earlier sheet"


def test_skip_cancelled_is_worked_out_again() -> None:
    # Cancelled on the earlier sheet, back on in Outlook: the owner's 'yes' wins but is flagged.
    row = fresh(people=10, ticket="CLASSIC", action=ACTION_BOOK)
    new, _, differs = carry_row(row, earlier(row, cancelled="yes", action=ACTION_SKIP))
    assert differs
    assert new.cancelled is True
    assert new.action == ACTION_CHECK
    # Cancelled in Outlook since: worked out again as skip.
    row = fresh(cancelled=True, action=ACTION_SKIP, people=10)
    new, took, _ = carry_row(row, earlier(row, people="", ticket="CLASSIC"))
    assert took
    assert new.action == ACTION_SKIP


def test_carry_over_matches_rows_by_event_id() -> None:
    kept = fresh(event_id="uid-1")
    changed = fresh(event_id="uid-4", ticket="CLASSIC")
    added = fresh(event_id="uid-3", client="Bluebird")
    same = fresh(event_id="uid-5")
    old_sheet = review_csv(
        [
            fresh(event_id="uid-1"),
            fresh(event_id="uid-2", client="Sunshine", start=""),
            fresh(event_id="uid-4", ticket="CLASSIC"),
            same,
        ]
    )
    earlier_rows = read_earlier_sheet(old_sheet)
    earlier_rows["uid-1"]["people"] = "12"
    earlier_rows["uid-1"]["ticket"] = "CLASSIC"
    earlier_rows["uid-4"]["ticket"] = "PREMIUM"
    result = carry_over((kept, changed, added, same), earlier_rows)
    assert [row.people for row in result.rows] == [12, None, None, None]
    assert result.rows[3] == same
    assert result.carried == ("uid-1", "uid-4")
    assert result.disagree == ("uid-4",)
    assert result.new == ("uid-3",)
    assert result.gone == ("2026-12-01 all day Sunshine",)


def test_reading_the_earlier_sheet() -> None:
    text = (
        "event_id,date,client,subject\n"
        "uid-1,2026-12-01,First,\n"
        "uid-1,2026-12-01,Second,\n"
        ",2026-12-01,No id,\n"
        "uid-2,2026-12-02,,[Soft Block] Someone\n"
    )
    rows = read_earlier_sheet(text)
    assert list(rows) == ["uid-1", "uid-2"]
    assert rows["uid-1"]["client"] == "First"
    result = carry_over((), rows)
    assert result.gone == ("2026-12-01 all day First", "2026-12-02 all day [Soft Block] Someone")
    with pytest.raises(CarryOverError, match="no event_id column"):
        read_earlier_sheet("date,client\n2026-12-01,Tan\n")
