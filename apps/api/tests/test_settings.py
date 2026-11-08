"""Business settings (SPEC §2) and per-date overrides, through the JSON API."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from jiak_api import models
from jiak_api.db import Database

DATE = "2026-12-01"
SALES = {"X-Role": "Sales"}

DEFAULTS = {
    "turnaroundMinutes": 0,
    "smallGroupWarnBelow": 0,
    "alternativeDaySearch": 7,
    "availabilityCalendarDays": 60,
    "prices": None,
}


def book(client: TestClient, start: str, ticket: str = "CLASSIC", adults: int = 5) -> str:
    body = {
        "date": DATE,
        "start": start,
        "type": ticket,
        "adults": adults,
        "customer": {"name": "Tan family"},
    }
    response = client.post("/api/bookings", json=body)
    assert response.status_code == 201
    booking_id: str = response.json()["id"]
    return booking_id


def settings(**changes: Any) -> dict[str, Any]:
    return {**DEFAULTS, **changes}


def audit_actions(database: Database) -> list[str]:
    with database.make_session() as db:
        return list(db.scalars(select(models.AuditLog.action).order_by(models.AuditLog.id)))


# --- app settings ----------------------------------------------------------------------------


def test_defaults_until_saved(client: TestClient) -> None:
    response = client.get("/api/settings", headers=SALES)  # everyone may read them
    assert response.status_code == 200
    assert response.json() == DEFAULTS


def test_save_and_read_back(client: TestClient, database: Database) -> None:
    new = settings(
        turnaroundMinutes=15,
        alternativeDaySearch=3,
        prices={"PREMIUM": {"adultCents": 9800, "childCents": 4900}},
    )
    response = client.put("/api/settings", json=new, headers={"X-User": "olivia"})
    assert response.status_code == 200
    assert response.json() == new
    assert client.get("/api/settings").json() == new
    assert audit_actions(database) == ["settings.update"]


def test_settings_change_the_answers(client: TestClient) -> None:
    """With a 30-minute reset, a session ending 10:30 blocks its zone until 11:00."""
    for _ in range(4):
        book(client, "09:00")  # CHE 09:00-10:30, all 4 zones
    params = {"date": DATE, "start": "10:30", "type": "CLASSIC", "adults": 4}
    assert client.get("/api/enquiries/check", params=params).json()["verdict"] != "NOT_POSSIBLE"

    assert client.put("/api/settings", json=settings(turnaroundMinutes=30)).status_code == 200
    assert client.get("/api/enquiries/check", params=params).json()["verdict"] == "NOT_POSSIBLE"


def test_turnaround_that_breaks_existing_bookings_is_refused(
    client: TestClient, database: Database
) -> None:
    for _ in range(4):
        book(client, "09:00")  # ends 10:30
        book(client, "10:30")  # back to back in every zone
    response = client.put("/api/settings", json=settings(turnaroundMinutes=15))
    assert response.status_code == 409
    assert DATE in response.json()["detail"]
    assert client.get("/api/settings").json()["turnaroundMinutes"] == 0  # unchanged
    assert audit_actions(database).count("settings.update") == 0


def test_past_days_do_not_block_a_change(database: Database, client: TestClient) -> None:
    """Back-to-back sessions on a day that is over don't matter any more."""
    with database.make_session() as db:
        for start in (9 * 60, 10 * 60 + 30):
            for _ in range(4):
                booking = models.Booking(
                    customer=models.Customer(name="Old group"),
                    date="2026-10-01",
                    start=start,
                    ticket_type="CLASSIC",
                    adults=5,
                    children=0,
                    created_by="test",
                )
                booking.sessions.append(
                    models.Session(
                        date="2026-10-01", start=start, ticket_type="CLASSIC", adults=5, children=0
                    )
                )
                db.add(booking)
        db.commit()
    assert client.put("/api/settings", json=settings(turnaroundMinutes=15)).status_code == 200


def test_settings_need_operations(client: TestClient) -> None:
    assert client.put("/api/settings", json=DEFAULTS, headers=SALES).status_code == 403


def test_settings_are_checked(client: TestClient) -> None:
    def status(**changes: Any) -> int:
        return client.put("/api/settings", json=settings(**changes)).status_code

    assert status(turnaroundMinutes=-5) == 422
    assert status(turnaroundMinutes=121) == 422
    assert status(smallGroupWarnBelow=26) == 422
    assert status(alternativeDaySearch=31) == 422
    assert status(availabilityCalendarDays=0) == 422
    assert status(availabilityCalendarDays=184) == 422
    assert status(prices={"DELUXE": {"adultCents": 1, "childCents": 1}}) == 422
    assert status(prices={"PREMIUM": {"adultCents": -1, "childCents": 0}}) == 422
    incomplete = dict(DEFAULTS)
    del incomplete["turnaroundMinutes"]
    assert client.put("/api/settings", json=incomplete).status_code == 422  # PUT = all fields


# --- per-date overrides ----------------------------------------------------------------------


def test_close_a_day(client: TestClient, database: Database) -> None:
    response = client.put(f"/api/days/{DATE}/settings", json={"closed": True, "blackouts": []})
    assert response.status_code == 200
    assert response.json()["closed"] is True
    params = {"date": DATE, "start": "10:00", "type": "CLASSIC", "adults": 4}
    assert client.get("/api/enquiries/check", params=params).json()["verdict"] == "NOT_POSSIBLE"
    assert audit_actions(database) == ["day.settings"]

    reopened = client.put(f"/api/days/{DATE}/settings", json={"closed": False, "blackouts": []})
    assert reopened.json()["closed"] is False


def test_blackout(client: TestClient) -> None:
    body = {"closed": False, "blackouts": [{"start": "14:00", "end": "17:00"}]}
    day = client.put(f"/api/days/{DATE}/settings", json=body).json()
    assert day["blackouts"] == [{"start": "14:00", "end": "17:00"}]
    board = client.get("/api/availability", params={"date": DATE, "n": 4}).json()
    row = next(r for r in board["rows"] if r["start"] == "15:00")
    assert set(row["statuses"].values()) == {"not_offered"}


def test_cannot_close_a_day_with_bookings(client: TestClient) -> None:
    book(client, "10:00")
    response = client.put(f"/api/days/{DATE}/settings", json={"closed": True, "blackouts": []})
    assert response.status_code == 409
    assert "booking" in response.json()["detail"].lower()
    assert client.get(f"/api/days/{DATE}").json()["closed"] is False


def test_cannot_black_out_a_booked_time(client: TestClient) -> None:
    book(client, "14:00")  # CHE 14:00-15:30
    body = {"closed": False, "blackouts": [{"start": "15:00", "end": "17:00"}]}
    assert client.put(f"/api/days/{DATE}/settings", json=body).status_code == 409
    body = {"closed": False, "blackouts": [{"start": "15:30", "end": "17:00"}]}
    assert client.put(f"/api/days/{DATE}/settings", json=body).status_code == 200


def test_day_settings_keep_the_lock(client: TestClient) -> None:
    client.post(f"/api/days/{DATE}/lock")
    body = {"closed": False, "blackouts": [{"start": "18:00", "end": "21:00"}]}
    day = client.put(f"/api/days/{DATE}/settings", json=body).json()
    assert day["locked"] is True


def test_day_settings_are_checked(client: TestClient) -> None:
    def status(blackouts: list[dict[str, str]], date: str = DATE) -> int:
        body = {"closed": False, "blackouts": blackouts}
        return client.put(f"/api/days/{date}/settings", json=body).status_code

    assert status([{"start": "17:00", "end": "14:00"}]) == 422  # backwards
    assert status([{"start": "08:00", "end": "10:00"}]) == 422  # before opening
    assert status([{"start": "20:00", "end": "22:00"}]) == 422  # after closing
    assert status([{"start": "2pm", "end": "17:00"}]) == 422
    assert status([], date="2026-02-30") == 422
    sales = client.put(
        f"/api/days/{DATE}/settings", json={"closed": True, "blackouts": []}, headers=SALES
    )
    assert sales.status_code == 403
