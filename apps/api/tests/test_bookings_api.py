"""Enquiry check, booking, cancelling and the day view, through the JSON API."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from jiak_api import models
from jiak_api.bookings import split_group
from jiak_api.db import Database

DATE = "2026-12-01"


def enquiry(
    start: str = "10:00", ticket: str = "PREMIUM", adults: int = 10, **extra: Any
) -> dict[str, Any]:
    return {"date": DATE, "start": start, "type": ticket, "adults": adults, **extra}


def booking(
    start: str = "10:00", ticket: str = "PREMIUM", adults: int = 10, **extra: Any
) -> dict[str, Any]:
    return {**enquiry(start, ticket, adults, **extra), "customer": {"name": "Tan family"}}


def count(database: Database, table: type[models.Base]) -> int:
    with database.make_session() as db:
        return db.scalar(select(func.count()).select_from(table)) or 0


# --- enquiry check ---------------------------------------------------------------------------


def test_check_matches_engine_t1(client: TestClient, database: Database) -> None:
    response = client.get("/api/enquiries/check", params=enquiry())
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["verdict"] == "ACCEPT_COSTLY"
    assert body["deadMinutesDelta"] == 60
    assert body["placement"] == {"kind": "new", "people": 10, "newSessions": None}
    first = body["alternatives"][0]
    assert (first["start"], first["type"], first["verdict"]) == ("10:30", "PREMIUM", "ACCEPT_GOOD")
    # Checking stores nothing (no hold state).
    assert count(database, models.Booking) == 0


def test_check_rejects_bad_input(client: TestClient) -> None:
    assert client.get("/api/enquiries/check", params=enquiry(start="10am")).status_code == 422
    assert client.get("/api/enquiries/check", params=enquiry(ticket="DELUXE")).status_code == 422
    assert client.get("/api/enquiries/check", params=enquiry(adults=-1)).status_code == 422


def test_check_works_as_a_plain_url(client: TestClient) -> None:
    url = f"/api/enquiries/check?date={DATE}&start=10:00&type=PREMIUM&adults=10&children=2"
    body = client.get(url).json()
    assert body["placement"]["people"] == 12


def test_check_needs_the_required_fields(client: TestClient) -> None:
    assert client.get(f"/api/enquiries/check?date={DATE}&start=10:00").status_code == 422


def test_check_explains_invalid_requests(client: TestClient) -> None:
    body = client.get("/api/enquiries/check", params=enquiry(start="19:30")).json()
    assert body["verdict"] == "NOT_POSSIBLE"
    assert "21:00" in body["reason"]


# --- booking ---------------------------------------------------------------------------------


def test_booking_creates_floating_session_and_audit_entry(
    client: TestClient, database: Database
) -> None:
    response = client.post("/api/bookings", json=booking(children=2), headers={"X-User": "alice"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["createdBy"] == "alice"
    assert body["customer"]["name"] == "Tan family"
    assert len(body["sessions"]) == 1
    session = body["sessions"][0]
    assert (session["adults"], session["children"], session["zoneId"]) == (10, 2, None)

    with database.make_session() as db:
        entry = db.scalars(select(models.AuditLog)).one()
        assert (entry.actor, entry.action, entry.entity_id) == (
            "alice",
            "booking.create",
            body["id"],
        )


def test_group_over_25_gets_one_session_per_zone(client: TestClient) -> None:
    body = client.post("/api/bookings", json=booking(adults=30, children=10)).json()
    sizes = [(s["adults"], s["children"]) for s in body["sessions"]]
    assert sorted(sizes) == [(5, 10), (25, 0)]


def test_fifth_group_in_a_slot_gets_409_with_alternatives(
    client: TestClient, database: Database
) -> None:
    for _ in range(4):
        assert client.post("/api/bookings", json=booking(adults=1)).status_code == 201

    response = client.post("/api/bookings", json=booking(adults=1))
    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == "All 4 zones are in use at 10:00."
    assert body["check"]["verdict"] == "NOT_POSSIBLE"
    assert body["check"]["alternatives"]  # sales gets something else to offer
    assert count(database, models.Booking) == 4


def test_cancel_frees_the_zone(client: TestClient, database: Database) -> None:
    ids = [client.post("/api/bookings", json=booking()).json()["id"] for _ in range(4)]
    assert client.post("/api/bookings", json=booking()).status_code == 409

    response = client.post(f"/api/bookings/{ids[0]}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["sessions"] == []
    assert count(database, models.Session) == 3

    assert client.post("/api/bookings", json=booking()).status_code == 201


def test_cancel_twice_or_unknown(client: TestClient) -> None:
    booking_id = client.post("/api/bookings", json=booking()).json()["id"]
    assert client.post(f"/api/bookings/{booking_id}/cancel").status_code == 200
    assert client.post(f"/api/bookings/{booking_id}/cancel").status_code == 409
    assert client.post("/api/bookings/nope/cancel").status_code == 404


def test_get_booking(client: TestClient) -> None:
    booking_id = client.post("/api/bookings", json=booking()).json()["id"]
    assert client.get(f"/api/bookings/{booking_id}").json()["id"] == booking_id
    assert client.get("/api/bookings/nope").status_code == 404


def test_unknown_role_is_rejected(client: TestClient) -> None:
    response = client.get("/api/enquiries/check", params=enquiry(), headers={"X-Role": "Guest"})
    assert response.status_code == 422


# --- day view --------------------------------------------------------------------------------


def test_day_view_shows_bookings_plan_and_metrics(client: TestClient) -> None:
    client.post("/api/bookings", json=booking(start="09:00", adults=25))
    cancelled = client.post("/api/bookings", json=booking(start="11:00")).json()["id"]
    client.post(f"/api/bookings/{cancelled}/cancel")

    body = client.get(f"/api/days/{DATE}").json()
    assert [b["start"] for b in body["bookings"]] == ["09:00"]  # cancelled ones are hidden
    session_id = body["bookings"][0]["sessions"][0]["id"]
    assert body["assignment"][session_id] in {"A", "B", "C", "D"}
    assert body["deadMinutes"] == 0
    assert body["metrics"]["averageFill"] == 1.0
    assert body["locked"] is False


def test_empty_day(client: TestClient) -> None:
    body = client.get(f"/api/days/{DATE}").json()
    assert body["bookings"] == []
    assert body["metrics"]["averageFill"] is None


# --- helpers ---------------------------------------------------------------------------------


def test_split_group_fills_zones_with_adults_first() -> None:
    assert split_group(10, 2) == [(10, 2)]
    assert split_group(30, 10) == [(25, 0), (5, 10)]
    assert split_group(0, 26) == [(0, 25), (0, 1)]
