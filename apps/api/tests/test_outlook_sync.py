"""App → Outlook: every booking change shows up in the Bookings calendar (SPEC §5.4).

These tests use the in-memory fake calendar; nothing talks to Microsoft.
"""

from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from graph.fake import FakeGateway
from graph.gateway import EventNotFound
from graph.model import CalendarEvent
from graph.real import GraphCalendar
from jiak_api import models
from jiak_api.clock import Now
from jiak_api.config import AppConfig, load_config
from jiak_api.db import Database
from jiak_api.main import create_app
from jiak_api.outlook_sync import make_gateway, sync_dates

DATE = "2026-12-01"
TODAY = Now(today="2026-11-07", minute=8 * 60)


@pytest.fixture
def calendar() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def app_client(database: Database, calendar: FakeGateway) -> TestClient:
    return TestClient(create_app(database, clock=lambda: TODAY, gateway=calendar))


def book(client: TestClient, **fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "date": DATE,
        "start": "11:00",
        "type": "PREMIUM_CT",
        "adults": 15,
        "children": 3,
        "customer": {"name": "Tan family", "phone": "9123 4567"},
        "notes": "Halal set for 2",
    }
    body.update(fields)
    response = client.post("/api/bookings", json=body)
    assert response.status_code == 201, response.text
    booking: dict[str, Any] = response.json()
    return booking


def stored_event_id(database: Database, booking_id: str) -> str | None:
    with database.make_session() as db:
        row = db.get(models.Booking, booking_id)
        assert row is not None
        return row.outlook_event_id


def only_event(calendar: FakeGateway) -> CalendarEvent:
    assert len(calendar.events) == 1
    return next(iter(calendar.events.values()))


def test_a_new_booking_gets_one_event(
    app_client: TestClient, calendar: FakeGateway, database: Database
) -> None:
    booking = book(app_client)
    event = only_event(calendar)
    assert event.booking_id == booking["id"]
    assert stored_event_id(database, booking["id"]) == event.id
    assert (event.date, event.start, event.end) == (DATE, 660, 840)
    # No route yet: the day hasn't been planned.
    assert event.subject == "PHE+CT · Tan family · 18 pax · 11:00–14:00"
    assert event.categories == ("PHE+CT",)
    assert "Contact: 9123 4567" in event.body
    assert "Notes: Halal set for 2" in event.body


def test_planning_the_day_adds_the_route(app_client: TestClient, calendar: FakeGateway) -> None:
    book(app_client)
    day = app_client.post(f"/api/days/{DATE}/allocate").json()
    route = day["bookings"][0]["sessions"][0]["route"]
    event = only_event(calendar)
    assert event.subject == f"PHE+CT · Tan family · 18 pax · {route} · 11:00–14:00"
    assert f"Route: {route}" in event.body
    assert calendar.calls == [("create", event.id), ("update", event.id)]


def test_syncing_again_changes_nothing(
    app_client: TestClient, calendar: FakeGateway, database: Database
) -> None:
    book(app_client)
    calendar.calls.clear()
    sync_dates(database, calendar, [DATE, DATE])
    assert calendar.calls == []


def test_split_group_lists_both_routes(app_client: TestClient, calendar: FakeGateway) -> None:
    book(app_client, type="PREMIUM", adults=40, children=0, customer={"name": "Lee wedding"})
    app_client.post(f"/api/days/{DATE}/lock")
    subject = only_event(calendar).subject
    assert subject.startswith("PHE · Lee wedding · 40 pax · ")
    routes = subject.split(" · ")[3]
    assert len(routes.split("/")) == 2


def test_an_edit_rewrites_the_same_event_even_on_a_new_date(
    app_client: TestClient, calendar: FakeGateway
) -> None:
    booking = book(app_client)
    event_id = only_event(calendar).id
    response = app_client.patch(
        f"/api/bookings/{booking['id']}", json={"date": "2026-12-02", "adults": 10}
    )
    assert response.status_code == 200, response.text
    event = only_event(calendar)
    assert event.id == event_id
    assert event.date == "2026-12-02"
    assert "· 13 pax ·" in event.subject


def test_cancelling_deletes_the_event(
    app_client: TestClient, calendar: FakeGateway, database: Database
) -> None:
    booking = book(app_client)
    event_id = only_event(calendar).id
    assert app_client.post(f"/api/bookings/{booking['id']}/cancel").status_code == 200
    assert calendar.events == {}
    assert ("delete", event_id) in calendar.calls
    assert stored_event_id(database, booking["id"]) is None


def test_a_no_show_keeps_the_event_and_says_so(database: Database, calendar: FakeGateway) -> None:
    sales = TestClient(create_app(database, clock=lambda: TODAY, gateway=calendar))
    booking = book(sales)
    after_start = Now(today=DATE, minute=12 * 60)
    ops = TestClient(create_app(database, clock=lambda: after_start, gateway=calendar))
    response = ops.post(f"/api/bookings/{booking['id']}/no-show", headers={"X-User": "olivia"})
    assert response.status_code == 200, response.text
    assert "Status: no-show" in only_event(calendar).body


def test_an_event_deleted_in_outlook_is_not_made_again(
    app_client: TestClient, calendar: FakeGateway, database: Database
) -> None:
    """Deleting it in Outlook means cancel: the sweep does that (test_outlook_inbound.py)."""
    booking = book(app_client)
    event_id = only_event(calendar).id
    calendar.delete_event(event_id)
    sync_dates(database, calendar, [DATE])
    assert calendar.events == {}
    assert stored_event_id(database, booking["id"]) == event_id


def test_an_event_changed_in_outlook_is_left_for_the_sweep(
    app_client: TestClient, calendar: FakeGateway, database: Database
) -> None:
    book(app_client)
    event = only_event(calendar)
    changed = CalendarEvent(
        id=event.id, date=DATE, start=600, end=700, subject="typed over", created_by="sales@x"
    )
    calendar.events[event.id] = changed
    sync_dates(database, calendar, [DATE])
    assert only_event(calendar) == changed


def test_outlook_details_are_kept_when_the_app_rewrites(
    app_client: TestClient, calendar: FakeGateway, database: Database
) -> None:
    booking = book(app_client)
    event = only_event(calendar)
    calendar.events[event.id] = replace(event, body="edited", created_by="sales@x")
    app_client.patch(f"/api/bookings/{booking['id']}", json={"notes": "Vegetarian x1"})
    rewritten = only_event(calendar)
    assert "Notes: Vegetarian x1" in rewritten.body
    assert rewritten.created_by == "sales@x"  # Outlook's own detail is kept


def test_the_web_pages_sync_too(app_client: TestClient, calendar: FakeGateway) -> None:
    form = {
        "date": DATE,
        "start": "10:00",
        "type": "CLASSIC",
        "adults": "6",
        "children": "0",
        "name": "Ong party",
    }
    response = app_client.post("/enquiry/book", data=form, follow_redirects=False)
    assert response.status_code == 303
    event = only_event(calendar)
    assert event.subject == "CHE · Ong party · 6 pax · 10:00–11:00"

    booking_id = response.headers["location"].rsplit("/", 1)[1]
    edit = {**form, "start": "12:00", "company": "ACME"}
    response = app_client.post(f"/bookings/{booking_id}/edit", data=edit, follow_redirects=False)
    assert response.status_code == 303
    assert only_event(calendar).subject == "CHE · Ong party (ACME) · 6 pax · 12:00–13:00"

    response = app_client.post(f"/ops/{DATE}/allocate", follow_redirects=False)
    assert response.status_code == 303
    assert only_event(calendar).subject.count(" · ") == 4  # the route was added

    response = app_client.post(f"/bookings/{booking_id}/cancel", follow_redirects=False)
    assert response.status_code == 303
    assert calendar.events == {}


class BrokenCalendar(FakeGateway):
    """A calendar that can't be reached, like Outlook during an outage."""

    def list_events(self, first_date: str, last_date: str) -> list[CalendarEvent]:
        raise ConnectionError("Outlook is down")


def test_an_outlook_outage_does_not_stop_bookings(
    database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    broken = BrokenCalendar()
    client = TestClient(create_app(database, clock=lambda: TODAY, gateway=broken))
    booking = book(client)
    assert booking["status"] == "confirmed"
    assert stored_event_id(database, booking["id"]) is None
    assert "Outlook sync of 2026-12-01 failed" in caplog.text

    # Once Outlook is back, the next sync of the date catches up.
    working = FakeGateway()
    sync_dates(database, working, [DATE])
    assert only_event(working).booking_id == booking["id"]


def test_outlook_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_TENANT_ID", " t ")
    monkeypatch.setenv("AZURE_CLIENT_ID", "c")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "s3cret")
    monkeypatch.setenv("BOOKINGS_MAILBOX", "jiak99-test@example.com")
    config = load_config()
    assert (config.azure_tenant_id, config.azure_client_id) == ("t", "c")
    assert (config.azure_client_secret, config.bookings_mailbox) == (
        "s3cret",
        "jiak99-test@example.com",
    )
    assert "s3cret" not in repr(config)


def test_fake_unless_graph_mode_is_real() -> None:
    assert isinstance(make_gateway(AppConfig(database_url="sqlite://")), FakeGateway)
    with pytest.raises(ValueError, match="GRAPH_MODE must be fake or real, not 'rael'"):
        make_gateway(AppConfig(database_url="sqlite://", graph_mode="rael"))
    with pytest.raises(ValueError, match="needs AZURE_CLIENT_SECRET, BOOKINGS_MAILBOX"):
        make_gateway(
            AppConfig(
                database_url="sqlite://",
                graph_mode="real",
                azure_tenant_id="t",
                azure_client_id="c",
            )
        )
    real = make_gateway(
        AppConfig(
            database_url="sqlite://",
            graph_mode="real",
            azure_tenant_id="t",
            azure_client_id="c",
            azure_client_secret="s3cret",
            bookings_mailbox="jiak99-test@example.com",
        )
    )
    assert isinstance(real, GraphCalendar)  # made, but nothing sent to Microsoft yet
    assert real.calendar_id() == "graph:jiak99-test@example.com"


def test_cancelled_booking_whose_event_is_already_gone(
    app_client: TestClient, calendar: FakeGateway, database: Database
) -> None:
    booking = book(app_client)
    event_id = only_event(calendar).id
    with database.make_session() as db:
        row = db.scalars(select(models.Booking)).one()
        row.status = "cancelled"
        db.commit()
    calendar.events.clear()
    sync_dates(database, calendar, [DATE])
    assert stored_event_id(database, booking["id"]) is None
    with pytest.raises(EventNotFound):
        calendar.get_event(event_id)
