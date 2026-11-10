"""The availability board and the 6-month calendar, through the JSON API."""

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from engine import add_days
from jiak_api import models
from jiak_api.db import Database
from jiak_api.seed import main as seed

DATE = "2026-12-01"


def book(client: TestClient, start: str, ticket: str, adults: int, date: str = DATE) -> str:
    body = {
        "date": date,
        "start": start,
        "type": ticket,
        "adults": adults,
        "customer": {"name": "Tan family"},
    }
    response = client.post("/api/bookings", json=body)
    assert response.status_code == 201
    booking_id: str = response.json()["id"]
    return booking_id


def statuses(board: dict[str, Any], start: str) -> dict[str, str]:
    row = next(r for r in board["rows"] if r["start"] == start)
    result: dict[str, str] = row["statuses"]
    return result


# --- availability board ----------------------------------------------------------------------


def test_board_matches_the_sales_board_of_the_cli(
    client: TestClient, database_url: str, database: Database
) -> None:
    assert seed(["--database-url", database_url, "--yes"]) == 0  # busy-day.json
    response = client.get("/api/availability", params={"date": DATE, "n": 12})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    board = response.json()
    assert board["people"] == 12
    assert board["closed"] is False
    assert [t["code"] for t in board["ticketTypes"]] == [
        "TINGKAT",
        "CLASSIC",
        "CLASSIC_CT",
        "PREMIUM",
        "PREMIUM_CT",
    ]
    assert board["ticketTypes"][4]["label"] == "PHE+CT"
    assert board["ticketTypes"][4]["minutes"] == 180
    # Same rows as `uv run check 2026-12-01 11:00 PREMIUM 12 --board sales`.
    assert set(statuses(board, "09:30").values()) == {"full"}
    assert set(statuses(board, "11:00").values()) == {"available"}
    assert len(board["rows"]) == 24  # 09:00 to 20:30


def test_board_marks_ticket_types_that_run_past_closing(client: TestClient) -> None:
    board = client.get("/api/availability", params={"date": DATE, "n": 4}).json()
    assert statuses(board, "19:30") == {
        "TINGKAT": "available",
        "CLASSIC": "available",
        "CLASSIC_CT": "available",  # 19:30 + 1.5 h ends at 21:00
        "PREMIUM": "not_offered",  # 19:30 + 2 h is after 21:00
        "PREMIUM_CT": "not_offered",
    }
    assert statuses(board, "20:30")["TINGKAT"] == "available"
    assert statuses(board, "20:30")["CLASSIC"] == "not_offered"


def test_board_follows_new_bookings(client: TestClient) -> None:
    for _ in range(4):
        book(client, "14:00", "PREMIUM", 2)
    board = client.get("/api/availability", params={"date": DATE, "n": 2}).json()
    assert set(statuses(board, "14:00").values()) == {"full"}
    assert set(statuses(board, "17:00").values()) == {"available"}


def test_board_needs_a_sensible_request(client: TestClient) -> None:
    assert client.get("/api/availability", params={"date": DATE, "n": 0}).status_code == 422
    assert client.get("/api/availability", params={"date": DATE, "n": 101}).status_code == 422
    assert client.get("/api/availability", params={"date": "2026-02-30", "n": 4}).status_code == 422
    assert client.get("/api/availability", params={"date": DATE}).status_code == 422


def test_board_of_a_closed_day(client: TestClient, database: Database) -> None:
    with database.make_session() as db:
        db.add(models.DaySettings(date=DATE, closed=True))
        db.commit()
    board = client.get("/api/availability", params={"date": DATE, "n": 4}).json()
    assert board["closed"] is True
    assert {s for row in board["rows"] for s in row["statuses"].values()} == {"not_offered"}


# --- calendar --------------------------------------------------------------------------------


def test_calendar_shows_every_day_in_the_range(client: TestClient) -> None:
    book(client, "09:00", "PREMIUM_CT", 25)  # 75 seat-hours
    book(client, "12:00", "CLASSIC", 8, date="2026-12-03")
    response = client.get("/api/calendar", params={"from": "2026-11-30", "to": "2026-12-06"})
    assert response.status_code == 200
    body = response.json()
    assert (body["from"], body["to"]) == ("2026-11-30", "2026-12-06")
    days = {d["date"]: d for d in body["days"]}
    assert list(days) == [add_days("2026-11-30", n) for n in range(7)]

    first = days[DATE]
    assert first["weekday"] == "Tue"
    assert (first["bookings"], first["people"]) == (1, 25)
    assert first["seatHourUse"] == pytest.approx(75 / 1200)
    assert first["zoneHourUse"] == pytest.approx(3 / 48)
    assert days["2026-12-03"]["people"] == 8
    empty = days["2026-11-30"]
    assert (empty["bookings"], empty["people"], empty["seatHourUse"]) == (0, 0, 0)
    assert empty["closed"] is False
    assert empty["locked"] is False


def test_calendar_counts_a_big_group_once(client: TestClient) -> None:
    book(client, "10:00", "PREMIUM", 60)  # 3 zones, 1 booking
    day = client.get("/api/calendar", params={"from": DATE, "to": DATE}).json()["days"][0]
    assert (day["bookings"], day["people"]) == (1, 60)
    assert day["zoneHourUse"] == pytest.approx(6 / 48)


def test_calendar_ignores_cancelled_and_no_show(client: TestClient) -> None:
    cancelled = book(client, "10:00", "PREMIUM", 10)
    client.post(f"/api/bookings/{cancelled}/cancel")
    day = client.get("/api/calendar", params={"from": DATE, "to": DATE}).json()["days"][0]
    assert (day["bookings"], day["people"], day["seatHourUse"]) == (0, 0, 0)


def test_calendar_shows_closed_and_locked_days(
    client: TestClient, database_url: str, database: Database
) -> None:
    assert seed(["week", "--database-url", database_url, "--yes"]) == 0
    with database.make_session() as db:
        day_row = db.get(models.DaySettings, "2026-12-07")
        assert day_row is not None
        day_row.locked_at = models.utc_now()
        db.commit()
    body = client.get("/api/calendar", params={"from": "2026-12-01", "to": "2026-12-08"}).json()
    days = {d["date"]: d for d in body["days"]}
    assert days["2026-12-08"]["closed"] is True
    assert days["2026-12-07"]["locked"] is True
    assert days["2026-12-02"]["seatHourUse"] > days["2026-12-01"]["seatHourUse"]


def test_calendar_needs_a_sensible_range(client: TestClient) -> None:
    def status(first: str, last: str) -> int:
        return client.get("/api/calendar", params={"from": first, "to": last}).status_code

    assert status("2026-12-05", "2026-12-01") == 422  # backwards
    assert status("2026-12-01", "2028-01-01") == 422  # more than a year
    assert status("2026-12-01", "2026-12-32") == 422  # not a date
    assert status("2026-12-01", "2027-05-31") == 200  # 6 months is fine


def test_six_month_calendar_loads_in_under_a_second(client: TestClient, database: Database) -> None:
    """SPEC §9: the 6-month calendar loads in < 1 s. 183 days x 8 bookings each."""
    with database.make_session() as db:
        for n in range(183):
            date = add_days(DATE, n)
            for k in range(8):
                start = 9 * 60 + 90 * (k // 4)
                booking = models.Booking(
                    customer=models.Customer(name=f"Group {k}"),
                    date=date,
                    start=start,
                    ticket_type="CLASSIC",
                    adults=10,
                    children=0,
                    created_by="test",
                )
                booking.sessions.append(
                    models.Session(
                        date=date, start=start, ticket_type="CLASSIC", adults=10, children=0
                    )
                )
                db.add(booking)
        db.commit()

    started = time.perf_counter()
    response = client.get("/api/calendar", params={"from": DATE, "to": add_days(DATE, 182)})
    elapsed = time.perf_counter() - started
    assert response.status_code == 200
    assert len(response.json()["days"]) == 183
    assert response.json()["days"][0]["bookings"] == 8
    assert elapsed < 1.0
