"""Allocating routes, locking the day and pinning sessions (Operations), through the JSON API."""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from engine.rules import ROUTES
from jiak_api import models
from jiak_api.db import Database
from jiak_api.seed import main as seed

DATE = "2026-12-01"
SALES = {"X-Role": "Sales"}


def book(
    client: TestClient, start: str = "10:00", ticket: str = "PREMIUM", adults: int = 10
) -> str:
    body = {
        "date": DATE,
        "start": start,
        "type": ticket,
        "adults": adults,
        "customer": {"name": "Tan family"},
    }
    response = client.post("/api/bookings", json=body)
    assert response.status_code == 201
    session_id: str = response.json()["sessions"][0]["id"]
    return session_id


def sessions_of(day: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """session id -> session, from a day view."""
    return {s["id"]: s for b in day["bookings"] for s in b["sessions"]}


def audit_actions(database: Database) -> list[str]:
    with database.make_session() as db:
        return list(db.scalars(select(models.AuditLog.action).order_by(models.AuditLog.id)))


# --- allocate --------------------------------------------------------------------------------


def test_allocate_stores_the_best_plan(client: TestClient, database: Database) -> None:
    for start in ("09:00", "11:00", "13:00"):
        book(client, start)
    before = client.get(f"/api/days/{DATE}").json()
    assert all(s["route"] is None for s in sessions_of(before).values())

    response = client.post(f"/api/days/{DATE}/allocate")
    assert response.status_code == 200
    day = response.json()
    routes = {sid: s["route"] for sid, s in sessions_of(day).items()}
    assert all(route in ROUTES["PREMIUM"] for route in routes.values())
    assert routes == day["assignment"]  # stored routes = the plan the day view shows
    assert day["deadMinutes"] == 0
    assert audit_actions(database)[-1] == "day.allocate"


def test_allocate_twice_changes_nothing(client: TestClient, database: Database) -> None:
    book(client)
    first = sessions_of(client.post(f"/api/days/{DATE}/allocate").json())
    second = sessions_of(client.post(f"/api/days/{DATE}/allocate").json())
    assert first == second
    assert audit_actions(database).count("day.allocate") == 1  # no change, no audit entry


def test_allocate_keeps_pinned_routes(client: TestClient, database_url: str) -> None:
    assert seed(["--database-url", database_url, "--yes"]) == 0  # 09:00 PHE+CT pinned to ABDC
    day = client.post(f"/api/days/{DATE}/allocate").json()
    pinned = [s for s in sessions_of(day).values() if s["pinned"]]
    assert [(s["route"], s["type"]) for s in pinned] == [("ABDC", "PREMIUM_CT")]
    assert all(s["route"] is not None for s in sessions_of(day).values())


def test_allocate_reports_clashing_pins(
    client: TestClient, database_url: str, tmp_path: Path
) -> None:
    day_file = tmp_path / "clash.json"
    day_file.write_text(
        '{"2026-12-01": ['
        '{"start": "09:00", "type": "PHE+CT", "adults": 5, "route": "ABCD", "pinned": true},'
        '{"start": "09:00", "type": "CHE", "adults": 5, "route": "AD", "pinned": true}]}',
        encoding="utf-8",
    )
    assert seed([str(day_file), "--database-url", database_url, "--yes"]) == 0
    response = client.post(f"/api/days/{DATE}/allocate")
    assert response.status_code == 409
    assert "check the pins" in response.json()["detail"]


def test_allocate_needs_operations(client: TestClient) -> None:
    book(client)
    assert client.post(f"/api/days/{DATE}/allocate", headers=SALES).status_code == 403


def test_allocate_an_empty_day(client: TestClient) -> None:
    response = client.post(f"/api/days/{DATE}/allocate")
    assert response.status_code == 200
    assert response.json()["bookings"] == []


def test_allocate_rejects_impossible_dates(client: TestClient) -> None:
    assert client.post("/api/days/2026-02-30/allocate").status_code == 422


# --- lock and unlock -------------------------------------------------------------------------


def test_lock_allocates_and_freezes_every_session(client: TestClient, database: Database) -> None:
    for start in ("09:00", "11:00"):
        book(client, start)
    response = client.post(f"/api/days/{DATE}/lock", headers={"X-User": "olivia"})
    assert response.status_code == 200
    day = response.json()
    assert day["locked"] is True
    for session in sessions_of(day).values():
        assert session["route"] in ROUTES["PREMIUM"]
        assert session["status"] == "locked"
    with database.make_session() as db:
        day_row = db.get(models.DaySettings, DATE)
        assert day_row is not None
        assert day_row.locked_at is not None
    assert audit_actions(database)[-1] == "day.lock"


def test_locked_routes_never_change(client: TestClient) -> None:
    first = book(client, "09:00", "CLASSIC")
    locked_route = sessions_of(client.post(f"/api/days/{DATE}/lock").json())[first]["route"]
    # More bookings and another allocation: the locked session keeps its route.
    for _ in range(2):
        book(client, "09:00", "PREMIUM")
    day = client.post(f"/api/days/{DATE}/allocate").json()
    sessions = sessions_of(day)
    assert sessions[first]["route"] == locked_route
    # Sessions booked after the lock get a route and are locked too, like the rest of the day.
    assert {s["status"] for s in sessions.values()} == {"locked"}
    assert all(s["route"] is not None for s in sessions.values())


def test_unlock_keeps_routes_but_lets_them_change(client: TestClient, database: Database) -> None:
    session_id = book(client)
    route = sessions_of(client.post(f"/api/days/{DATE}/lock").json())[session_id]["route"]
    response = client.post(f"/api/days/{DATE}/unlock")
    assert response.status_code == 200
    day = response.json()
    assert day["locked"] is False
    session = sessions_of(day)[session_id]
    assert (session["route"], session["status"]) == (route, "open")
    assert audit_actions(database)[-2:] == ["day.lock", "day.unlock"]


def test_lock_twice_or_unlock_an_open_day(client: TestClient) -> None:
    assert client.post(f"/api/days/{DATE}/unlock").status_code == 409
    assert client.post(f"/api/days/{DATE}/lock").status_code == 200
    assert client.post(f"/api/days/{DATE}/lock").status_code == 409


def test_unlock_keeps_pins(client: TestClient) -> None:
    session_id = book(client)
    client.patch(f"/api/sessions/{session_id}/pin", json={"route": "DCBA"})
    client.post(f"/api/days/{DATE}/lock")
    session = sessions_of(client.post(f"/api/days/{DATE}/unlock").json())[session_id]
    assert (session["route"], session["pinned"]) == ("DCBA", True)


def test_lock_and_unlock_need_operations(client: TestClient) -> None:
    assert client.post(f"/api/days/{DATE}/lock", headers=SALES).status_code == 403
    assert client.post(f"/api/days/{DATE}/unlock", headers=SALES).status_code == 403


def test_calendar_shows_the_lock(client: TestClient) -> None:
    client.post(f"/api/days/{DATE}/lock")
    day = client.get("/api/calendar", params={"from": DATE, "to": DATE}).json()["days"][0]
    assert day["locked"] is True


# --- pin -------------------------------------------------------------------------------------


def test_pin_to_a_route(client: TestClient, database: Database) -> None:
    session_id = book(client)
    response = client.patch(f"/api/sessions/{session_id}/pin", json={"route": "dcba"})
    assert response.status_code == 200
    day = response.json()
    session = sessions_of(day)[session_id]
    assert (session["route"], session["pinned"]) == ("DCBA", True)
    assert day["assignment"][session_id] == "DCBA"
    assert audit_actions(database)[-1] == "session.pin"


def test_pin_moves_unpinned_groups_out_of_the_way(client: TestClient) -> None:
    first = book(client)
    second = book(client)
    routes = sessions_of(client.post(f"/api/days/{DATE}/allocate").json())
    taken = routes[second]["route"]

    day = client.patch(f"/api/sessions/{first}/pin", json={"route": taken}).json()
    sessions = sessions_of(day)
    assert sessions[first]["route"] == taken
    assert sessions[second]["route"] not in {taken, None}  # re-routed, not left clashing


def test_pin_into_a_clash_is_refused(client: TestClient) -> None:
    first = book(client)
    second = book(client)
    assert client.patch(f"/api/sessions/{first}/pin", json={"route": "ABCD"}).status_code == 200

    response = client.patch(f"/api/sessions/{second}/pin", json={"route": "ABCD"})
    assert response.status_code == 409
    assert response.json()["detail"] == "Route ABCD clashes with a pinned or locked group."
    session = sessions_of(client.get(f"/api/days/{DATE}").json())[second]
    assert session["pinned"] is False  # nothing changed


def test_pin_to_the_current_route(client: TestClient) -> None:
    session_id = book(client)
    # Not allocated yet: there is no route to keep.
    response = client.patch(f"/api/sessions/{session_id}/pin", json={})
    assert response.status_code == 409
    assert "allocate" in response.json()["detail"].lower()

    route = sessions_of(client.post(f"/api/days/{DATE}/allocate").json())[session_id]["route"]
    day = client.patch(f"/api/sessions/{session_id}/pin", json={}).json()
    assert sessions_of(day)[session_id]["route"] == route
    assert sessions_of(day)[session_id]["pinned"] is True


def test_unpin_keeps_the_route(client: TestClient, database: Database) -> None:
    session_id = book(client)
    client.patch(f"/api/sessions/{session_id}/pin", json={"route": "BADC"})
    response = client.patch(f"/api/sessions/{session_id}/pin", json={"pinned": False})
    assert response.status_code == 200
    session = sessions_of(response.json())[session_id]
    assert (session["route"], session["pinned"]) == ("BADC", False)
    assert audit_actions(database)[-1] == "session.unpin"


def test_pinned_groups_stay_put_when_others_book(client: TestClient) -> None:
    session_id = book(client, "09:00", "CLASSIC")
    client.patch(f"/api/sessions/{session_id}/pin", json={"route": "BA"})
    for _ in range(2):
        book(client, "09:00", "PREMIUM")
    day = client.post(f"/api/days/{DATE}/allocate").json()
    assert sessions_of(day)[session_id]["route"] == "BA"


def test_pin_needs_operations_a_real_session_and_a_real_route(client: TestClient) -> None:
    session_id = book(client)
    url = f"/api/sessions/{session_id}/pin"
    assert client.patch(url, json={"route": "ABCD"}, headers=SALES).status_code == 403
    assert client.patch(url, json={"route": "ABCE"}).status_code == 422  # no zone E
    response = client.patch(url, json={"route": "AB"})  # a Classic route, not a Premium one
    assert response.status_code == 409
    assert response.json()["detail"] == "AB is not a route a PHE group can take."
    assert client.patch("/api/sessions/nope/pin", json={"route": "ABCD"}).status_code == 404
