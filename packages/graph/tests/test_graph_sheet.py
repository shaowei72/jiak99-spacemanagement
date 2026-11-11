"""Reading the corrected review sheet back in, and what the commit does with each row (SPEC §5.4
one-off take-over, step 4)."""

import pytest

from graph.ics import IcsEvent
from graph.sheet import (
    DECISION_BOOK,
    DECISION_HISTORY,
    DECISION_NOT_READY,
    DECISION_OVER_100,
    DECISION_SKIP,
    SheetError,
    SheetRow,
    decide,
    past_times,
    read_count,
    read_date,
    read_sheet,
    read_ticket,
    read_time,
    read_yes_no,
    split_headcount,
    ticket_source,
)
from graph.takeover import CSV_COLUMNS, build_review, review_csv

TODAY = "2026-11-11"
HEADER = ",".join(CSV_COLUMNS)


def sheet_line(**cells: str) -> str:
    """One CSV line with the given cells (others empty), in the sheet's column order."""
    defaults = {"event_id": "uid-1", "date": "2026-11-20", "start": "10:00", "end": "12:00"}
    defaults.update(cells)
    return ",".join(defaults.get(column, "") for column in CSV_COLUMNS)


def one_row(**cells: str) -> SheetRow:
    rows = read_sheet(HEADER + "\n" + sheet_line(**cells) + "\n")
    assert len(rows) == 1
    return rows[0]


# --- cells -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "date"),
    [
        ("2026-11-12", "2026-11-12"),
        ("12/11/2026", "2026-11-12"),  # Excel on a Singapore computer: day/month/year
        (" 1/2/2027 ", "2027-02-01"),
        ("31/02/2027", None),
        ("2026-13-01", None),
        ("11/12/26", None),
        ("", None),
    ],
)
def test_read_date(text: str, date: str | None) -> None:
    assert read_date(text) == date


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("08:30", 510),
        ("8:30", 510),
        ("8:30:00", 510),
        ("01:00+1d", 1500),
        ("24:00", None),
        ("10:60", None),
        ("8:30:15", None),
        ("half ten", None),
    ],
)
def test_read_time(text: str, minutes: int | None) -> None:
    assert read_time(text) == minutes


def test_read_count() -> None:
    assert read_count(" 18 ") == 18
    assert read_count("") is None
    with pytest.raises(ValueError):
        read_count("18 pax")
    with pytest.raises(ValueError):
        read_count("-1")


def test_read_ticket_takes_codes_and_short_names() -> None:
    assert read_ticket("premium_ct") == "PREMIUM_CT"
    assert read_ticket("PHE+CT") == "PREMIUM_CT"
    assert read_ticket(" che ") == "CLASSIC"
    assert read_ticket("") is None
    with pytest.raises(ValueError):
        read_ticket("PREMIUM PLUS")


def test_read_yes_no() -> None:
    assert read_yes_no("yes") is True
    assert read_yes_no("TRUE") is True  # Excel
    assert read_yes_no("no") is False
    assert read_yes_no("") is False
    with pytest.raises(ValueError):
        read_yes_no("maybe")


# --- the sheet -------------------------------------------------------------------------------


def test_the_dry_run_sheet_reads_back_unchanged() -> None:
    event = IcsEvent(
        uid="uid-7",
        date="2026-11-20",
        start=660,
        end=840,
        subject="[Soft Block] Bluebird Primary PHE + CT 18 pax",
    )
    text = review_csv(build_review([event], TODAY, []).rows)
    [row] = read_sheet(text)
    assert row.line == 2
    assert (row.event_id, row.date, row.start, row.end) == ("uid-7", "2026-11-20", 660, 840)
    assert (row.people, row.ticket, row.client) == (18, "PREMIUM_CT", "Bluebird Primary")
    assert row.soft_block is True
    assert row.cancelled is False
    assert row.ticket_found == "subject"
    assert row.problems == ()


def test_excel_resaved_sheet() -> None:
    row = one_row(date="20/11/2026", start="9:30", end="11:30:00", people="20", ticket="PHE")
    assert (row.date, row.start, row.end, row.people) == ("2026-11-20", 570, 690, 20)
    assert row.problems == ()


def test_unreadable_cells_are_listed_as_problems() -> None:
    row = one_row(
        event_id="",
        date="20.11.2026",
        start="ten",
        people="about 20",
        ticket="VIP",
        soft_block="perhaps",
    )
    assert row.problems == (
        "date '20.11.2026' is not a date like 2026-11-12",
        "start 'ten' is not a time like 10:30",
        "people 'about 20' is not a whole number",
        "ticket 'VIP' is not one of TINGKAT, CLASSIC, CLASSIC_CT, PREMIUM, PREMIUM_CT",
        "soft_block 'perhaps' should be yes or no",
        "event_id is empty (it links the row to its Outlook event)",
    )
    assert row.date == ""
    assert row.start is None


def test_empty_lines_are_skipped_and_line_numbers_kept() -> None:
    text = HEADER + "\n" + ",,,,\n" + sheet_line(event_id="b") + "\n"
    [row] = read_sheet(text)
    assert row.line == 3


def test_the_same_event_twice_is_a_problem() -> None:
    text = "\n".join([HEADER, sheet_line(), sheet_line(people="5")])
    first, second = read_sheet(text)
    assert first.problems == ()
    assert second.problems == ("same event_id as line 2",)


def test_a_missing_column_stops_the_sheet() -> None:
    with pytest.raises(SheetError, match="no people, ticket, soft_block,"):
        read_sheet("event_id,date,start,end,adults,children,client\n")
    with pytest.raises(SheetError, match="no event_id"):
        read_sheet("")


def test_extra_columns_and_short_lines_are_fine() -> None:
    lines = [
        HEADER + ",my remarks",
        sheet_line(event_id="uid-9") + ",call back",
        "2026-11-20,10:00",
    ]
    first, second = read_sheet("\n".join(lines))
    assert (first.event_id, first.start, first.problems) == ("uid-9", 600, ())
    assert (second.start, second.people, second.subject) == (600, None, "")
    assert second.problems == ("event_id is empty (it links the row to its Outlook event)",)


# --- deciding --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cells", "result"),
    [
        ({"people": "20"}, (20, 0)),
        ({"people": "20", "children": "5"}, (15, 5)),
        ({"people": "20", "adults": "18"}, (18, 2)),
        ({"people": "20", "adults": "18", "children": "2"}, (18, 2)),
        ({"adults": "3", "children": "2"}, (3, 2)),
        ({"children": "4"}, (0, 4)),
        ({}, "no headcount: fill in people"),
        (
            {"people": "20", "adults": "18", "children": "5"},
            "adults (18) and children (5) don't add up to people (20)",
        ),
        (
            {"people": "20", "adults": "25"},
            "adults (25) and children (None) don't add up to people (20)",
        ),
    ],
)
def test_split_headcount(cells: dict[str, str], result: tuple[int, int] | str) -> None:
    assert split_headcount(one_row(**cells)) == result


def test_a_complete_future_row_is_booked() -> None:
    decision = decide(one_row(people="30", children="4", ticket="PREMIUM"), TODAY)
    assert (decision.kind, decision.adults, decision.children) == (DECISION_BOOK, 26, 4)


def test_a_row_on_the_take_over_date_is_future() -> None:
    row = one_row(date=TODAY, people="10", ticket="CLASSIC")
    assert decide(row, TODAY).kind == DECISION_BOOK


@pytest.mark.parametrize(
    ("cells", "kind", "reason"),
    [
        ({"action": "skip", "people": "x"}, DECISION_SKIP, "skip on the sheet"),
        ({"action": "Skip (cancelled)"}, DECISION_SKIP, "skip on the sheet"),
        ({"people": "x"}, DECISION_NOT_READY, "people 'x' is not a whole number"),
        ({"date": "2026-11-01"}, DECISION_HISTORY, ""),  # past: no headcount needed
        ({"date": "2026-11-01", "cancelled": "yes"}, DECISION_HISTORY, ""),
        ({"cancelled": "yes", "people": "10"}, DECISION_SKIP, "cancelled"),
        (
            {"start": "", "people": "10", "ticket": "CLASSIC"},
            DECISION_NOT_READY,
            "no start time (all-day event): fill in start",
        ),
        (
            {"start": "10:15", "people": "10", "ticket": "CLASSIC"},
            DECISION_NOT_READY,
            "start is not on the half hour",
        ),
        ({"ticket": "CLASSIC"}, DECISION_NOT_READY, "no headcount: fill in people"),
        (
            {"people": "110"},  # no ticket needed: it isn't booked anyway
            DECISION_OVER_100,
            "110 people: not booked; block the venue with a blackout on the Day plan",
        ),
        ({"people": "0", "ticket": "CLASSIC"}, DECISION_NOT_READY, "people must be at least 1"),
        ({"people": "10"}, DECISION_NOT_READY, "no ticket type: fill in ticket"),
    ],
)
def test_decide(cells: dict[str, str], kind: str, reason: str) -> None:
    decision = decide(one_row(**cells), TODAY)
    assert (decision.kind, decision.reason) == (kind, reason)


@pytest.mark.parametrize(
    ("cells", "source"),
    [
        ({}, None),
        ({"ticket": "TINGKAT", "ticket_found": "client map: Sunshine"}, "client"),
        ({"ticket": "PREMIUM", "ticket_found": "subject"}, "subject"),
        ({"ticket": "PREMIUM", "ticket_found": "body"}, "body"),
        ({"ticket": "PREMIUM"}, "review"),
    ],
)
def test_ticket_source(cells: dict[str, str], source: str | None) -> None:
    assert ticket_source(one_row(**cells)) == source


def test_past_times() -> None:
    assert past_times(one_row(start="10:00", end="12:00")) == (600, 720)
    assert past_times(one_row(start="", end="")) == (0, 1440)  # all day
    assert past_times(one_row(start="10:00", end="")) == (600, 600)
    assert past_times(one_row(start="10:00", end="09:00")) == (600, 600)
