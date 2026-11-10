"""Editing a booking (PATCH, re-checked) and marking a no-show, through the JSON API."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from jiak_api import bookings, models
from jiak_api.clock import Now
from jiak_api.db import Database
from jiak_api.main import create_app

DATE = "2026-12-01"


def booking(
    start: str = "10:00", ticket: str = "PREMIUM", adults: int = 10, **extra: Any
) -> dict[str, Any]:
    return {
        "date": DATE,
        "start": start,
        "type": ticket,
        "adults": adults,
        "customer": {"name": "Tan family"},
        **extra,
    }


def book(client: TestClient, **fields: Any) -> dict[str, Any]:
    response = client.post("/api/bookings", json=booking(**fields))
    assert response.status_code == 201
    body: dict[str, Any] = response.json()
    return body


def audit_actions(database: Database) -> list[str]:
    with database.make_session() as db:
        return list(db.scalars(select(models.AuditLog.action).order_by(models.AuditLog.id)))


def client_at(database: Database, today: str, minute: int) -> TestClient:
    """A client whose clock says `today` at `minute` (minutes since midnight)."""
    return TestClient(create_app(database, clock=lambda: Now(today=today, minute=minute)))


# --- edit ------------------------------------------------------------------------------------


def test_edit_headcount_keeps_the_same_session(client: TestClient, database: Database) -> None:
    original = book(client)
    response = client.patch(
        f"/api/bookings/{original['id']}",
        json={"adults": 12, "children": 3},
        headers={"X-User": "bob"},
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["adults"], body["children"]) == (12, 3)
    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["id"] == original["sessions"][0]["id"]
    assert (body["sessions"][0]["adults"], body["sessions"][0]["children"]) == (12, 3)
    assert audit_actions(database) == ["booking.create", "booking.edit"]


def test_edit_does_not_clash_with_its_own_zone(client: TestClient) -> None:
    ids = [book(client, adults=5)["id"] for _ in range(4)]  # slot sold out
    response = client.patch(f"/api/bookings/{ids[0]}", json={"adults": 20})
    assert response.status_code == 200


def test_edit_into_a_full_slot_gets_409_and_changes_nothing(
    client: TestClient, database: Database
) -> None:
    for _ in range(4):
        book(client, start="14:00")
    mine = book(client, start="10:00")

    response = client.patch(f"/api/bookings/{mine['id']}", json={"start": "14:00"})
    assert response.status_code == 409
    body = response.json()
    assert body["check"]["verdict"] == "NOT_POSSIBLE"
    assert body["check"]["alternatives"]

    after = client.get(f"/api/bookings/{mine['id']}").json()
    assert after["start"] == "10:00"
    assert after["sessions"][0]["id"] == mine["sessions"][0]["id"]
    assert audit_actions(database).count("booking.edit") == 0


def test_moving_a_booking_frees_its_old_slot(client: TestClient) -> None:
    ids = [book(client)["id"] for _ in range(4)]
    assert client.post("/api/bookings", json=booking()).status_code == 409

    response = client.patch(f"/api/bookings/{ids[0]}", json={"start": "14:00"})
    assert response.status_code == 200
    assert response.json()["sessions"][0]["start"] == "14:00"

    assert client.post("/api/bookings", json=booking()).status_code == 201


def test_moving_to_another_date_and_ticket_type(client: TestClient) -> None:
    mine = book(client)
    response = client.patch(
        f"/api/bookings/{mine['id']}",
        json={"date": "2026-12-02", "start": "11:00", "type": "PREMIUM_CT"},
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["date"], body["start"], body["type"]) == ("2026-12-02", "11:00", "PREMIUM_CT")
    assert body["sessions"][0]["type"] == "PREMIUM_CT"
    assert body["sessions"][0]["id"] != mine["sessions"][0]["id"]  # new floating session

    assert client.get(f"/api/days/{DATE}").json()["bookings"] == []
    assert len(client.get("/api/days/2026-12-02").json()["bookings"]) == 1


def test_growing_past_25_splits_the_group(client: TestClient) -> None:
    mine = book(client, adults=20)
    body = client.patch(f"/api/bookings/{mine['id']}", json={"adults": 30}).json()
    sizes = sorted((s["adults"], s["children"]) for s in body["sessions"])
    assert sizes == [(15, 0), (15, 0)]


def test_edit_notes_and_customer_only(client: TestClient) -> None:
    mine = book(client)
    response = client.patch(
        f"/api/bookings/{mine['id']}",
        json={
            "notes": "Birthday cake at 11:00",
            "customer": {"name": "Tan family", "phone": "9123"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["notes"] == "Birthday cake at 11:00"
    assert body["customer"]["phone"] == "9123"
    assert body["sessions"][0]["id"] == mine["sessions"][0]["id"]


def test_notes_can_change_after_the_visit(database: Database) -> None:
    setup = client_at(database, "2026-11-07", 8 * 60)
    mine = book(setup)
    later = client_at(database, "2026-12-05", 9 * 60)  # booking is in the past now
    response = later.patch(f"/api/bookings/{mine['id']}", json={"notes": "Paid by card"})
    assert response.status_code == 200


def test_edit_with_nothing_changed_writes_no_audit(client: TestClient, database: Database) -> None:
    mine = book(client)
    assert client.patch(f"/api/bookings/{mine['id']}", json={"start": "10:00"}).status_code == 200
    assert audit_actions(database) == ["booking.create"]


def test_edit_cancelled_or_unknown_booking(client: TestClient) -> None:
    mine = book(client)
    client.post(f"/api/bookings/{mine['id']}/cancel")
    assert client.patch(f"/api/bookings/{mine['id']}", json={"adults": 2}).status_code == 409
    assert client.patch("/api/bookings/nope", json={"adults": 2}).status_code == 404


def test_booking_moved_by_someone_else_while_waiting(
    client: TestClient, database: Database
) -> None:
    """We locked the date we read, but meanwhile another edit moved the booking elsewhere."""
    mine = book(client)
    with database.make_session() as db:
        row = db.get(models.Booking, mine["id"])
        assert row is not None
        with pytest.raises(bookings.BookingChangedMeanwhile):
            bookings.refresh_under_lock(db, row, locked_dates=["2026-12-02"])


def test_edit_rejects_bad_input(client: TestClient) -> None:
    mine = book(client)
    url = f"/api/bookings/{mine['id']}"
    assert client.patch(url, json={"start": "10am"}).status_code == 422
    assert client.patch(url, json={"adults": -1}).status_code == 422
    assert client.patch(url, json={"type": "DELUXE"}).status_code == 422


# --- no-show ---------------------------------------------------------------------------------


def test_no_show_frees_the_zone_and_stays_visible(database: Database) -> None:
    setup = client_at(database, "2026-11-07", 8 * 60)
    ids = [book(setup)["id"] for _ in range(4)]
    ops = client_at(database, DATE, 10 * 60 + 15)  # 10:15 on the day

    response = ops.post(f"/api/bookings/{ids[0]}/no-show", headers={"X-User": "olivia"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "no_show"
    assert body["sessions"] == []
    assert audit_actions(database)[-1] == "booking.no_show"

    day = ops.get(f"/api/days/{DATE}").json()
    statuses = sorted(b["status"] for b in day["bookings"])
    assert statuses == ["confirmed", "confirmed", "confirmed", "no_show"]


def test_no_show_only_after_the_start_time(client: TestClient, database: Database) -> None:
    mine = book(client)  # the default clock is 2026-11-07, weeks before the booking
    response = client.post(f"/api/bookings/{mine['id']}/no-show")
    assert response.status_code == 409
    assert "cancel" in response.json()["detail"].lower()

    ops = client_at(database, DATE, 9 * 60 + 59)  # one minute too early
    assert ops.post(f"/api/bookings/{mine['id']}/no-show").status_code == 409
    ops = client_at(database, DATE, 10 * 60)
    assert ops.post(f"/api/bookings/{mine['id']}/no-show").status_code == 200


def test_no_show_needs_operations(database: Database) -> None:
    setup = client_at(database, "2026-11-07", 8 * 60)
    mine = book(setup)
    ops = client_at(database, "2026-12-02", 9 * 60)  # the day after
    response = ops.post(f"/api/bookings/{mine['id']}/no-show", headers={"X-Role": "Sales"})
    assert response.status_code == 403


def test_no_show_twice_or_unknown(database: Database) -> None:
    setup = client_at(database, "2026-11-07", 8 * 60)
    mine = book(setup)
    ops = client_at(database, "2026-12-02", 9 * 60)
    assert ops.post(f"/api/bookings/{mine['id']}/no-show").status_code == 200
    assert ops.post(f"/api/bookings/{mine['id']}/no-show").status_code == 409
    assert ops.post(f"/api/bookings/{mine['id']}/cancel").status_code == 409
    assert ops.patch(f"/api/bookings/{mine['id']}", json={"notes": "x"}).status_code == 409
    assert ops.post("/api/bookings/nope/no-show").status_code == 404
