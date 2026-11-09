"""Bookings list, and edit / cancel / no-show from the web pages."""

from typing import Any

from fastapi.testclient import TestClient

from jiak_api.clock import Now
from jiak_api.db import Database
from jiak_api.main import create_app

DAY = "2026-12-01"
SALES = {"X-Role": "Sales"}


def book(
    client: TestClient,
    start: str,
    name: str,
    *,
    date: str = DAY,
    ticket_type: str = "PREMIUM",
    adults: int = 10,
    company: str | None = None,
) -> dict[str, Any]:
    body = {
        "date": date,
        "start": start,
        "type": ticket_type,
        "adults": adults,
        "customer": {"name": name, "company": company},
    }
    response = client.post("/api/bookings", json=body)
    assert response.status_code == 201, response.text
    booking: dict[str, Any] = response.json()
    return booking


def edit_form(**changes: str) -> dict[str, str]:
    form = {
        "date": DAY,
        "start": "10:00",
        "type": "PREMIUM",
        "adults": "10",
        "children": "0",
        "name": "Tan family",
        "phone": "",
        "email": "",
        "company": "",
        "notes": "",
    }
    form.update(changes)
    return form


# --- the list --------------------------------------------------------------------------------


def test_bookings_is_in_the_menu(client: TestClient) -> None:
    assert '<a href="/bookings">Bookings</a>' in client.get("/enquiry").text


def test_list_shows_bookings_from_today_grouped_by_date(client: TestClient) -> None:
    book(client, "14:00", "Lim office", date="2026-12-02")
    book(client, "10:00", "Tan family")
    book(client, "09:00", "Early bird")
    page = client.get("/bookings")
    assert page.status_code == 200
    text = page.text
    assert "3 bookings" in text
    assert text.index("Tue 2026-12-01") < text.index("Wed 2026-12-02")
    assert text.index("Early bird") < text.index("Tan family") < text.index("Lim office")
    assert 'href="/ops?date=2026-12-01"' in text


def test_list_filters_by_dates_and_name(client: TestClient) -> None:
    book(client, "10:00", "Tan family")
    book(client, "11:00", "Mr Lee", company="Acme TANKERS")
    book(client, "12:00", "Ng", date="2026-12-05")
    page = client.get("/bookings?from=2026-12-01&to=2026-12-01&name=tan").text
    assert "Tan family" in page
    assert "Mr Lee" in page  # company matches, in any case
    assert "Ng" not in page
    assert "2 bookings" in page
    assert "No bookings match." in client.get("/bookings?name=nobody").text


def test_list_hides_cancelled_bookings_unless_asked(client: TestClient) -> None:
    booking = book(client, "10:00", "Gone away")
    client.post(f"/api/bookings/{booking['id']}/cancel")
    assert "Gone away" not in client.get("/bookings").text
    page = client.get("/bookings?status=all").text
    assert "Gone away" in page
    assert "cancelled" in page


def test_list_explains_bad_dates(client: TestClient) -> None:
    page = client.get("/bookings?from=2026-02-30&to=nope").text
    assert "From&#39; must be a real date." in page  # the quotes come from the message
    assert "To&#39; must be a real date." in page


# --- the booking page ------------------------------------------------------------------------


def test_booking_page_has_edit_cancel_and_links(client: TestClient) -> None:
    booking = book(client, "10:00", "Tan family")
    page = client.get(f"/bookings/{booking['id']}").text
    assert f'href="/bookings/{booking["id"]}/edit"' in page
    assert f'href="/bookings/{booking["id"]}/cancel"' in page
    assert 'href="/ops?date=2026-12-01"' in page
    assert "Mark no-show" not in page  # the visit hasn't started


# --- edit ------------------------------------------------------------------------------------


def test_edit_form_is_filled_in(client: TestClient) -> None:
    booking = book(client, "10:00", "Tan family", company="Tan Pte Ltd")
    page = client.get(f"/bookings/{booking['id']}/edit").text
    assert 'value="10:00"' in page
    assert 'value="Tan Pte Ltd"' in page


def test_edit_moves_the_booking(client: TestClient) -> None:
    booking = book(client, "10:00", "Tan family")
    form = edit_form(start="14:00", children="2", notes="Birthday", phone="9123 4567")
    response = client.post(f"/bookings/{booking['id']}/edit", data=form, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/bookings/{booking['id']}?done=changed"
    saved = client.get(f"/api/bookings/{booking['id']}").json()
    assert saved["start"] == "14:00"
    assert saved["children"] == 2
    assert saved["notes"] == "Birthday"
    assert saved["customer"]["phone"] == "9123 4567"
    assert "Booking changed." in client.get(response.headers["location"]).text


def test_edit_that_doesnt_fit_changes_nothing_and_offers_alternatives(
    client: TestClient,
) -> None:
    for name in ("A", "B", "C", "D"):
        book(client, "14:00", f"Afternoon {name}", adults=25)
    booking = book(client, "10:00", "Tan family")
    response = client.post(f"/bookings/{booking['id']}/edit", data=edit_form(start="14:00"))
    assert response.status_code == 409
    assert "The change doesn't fit" in response.text
    assert "Use this" in response.text
    assert client.get(f"/api/bookings/{booking['id']}").json()["start"] == "10:00"


def test_use_this_fills_the_form(client: TestClient) -> None:
    booking = book(client, "10:00", "Tan family")
    url = f"/bookings/{booking['id']}/edit?date=2026-12-02&start=11:30&type=CLASSIC&adults=12&children=1"
    page = client.get(url).text
    assert 'value="2026-12-02"' in page
    assert 'value="11:30"' in page
    assert 'value="12"' in page
    assert '<option value="CLASSIC" selected>' in page


def test_edit_mistakes_are_explained(client: TestClient) -> None:
    booking = book(client, "10:00", "Tan family")
    response = client.post(f"/bookings/{booking['id']}/edit", data=edit_form(name="", adults="x"))
    assert response.status_code == 422
    assert "Enter the customer&#39;s name." in response.text
    assert "Adults must be a whole number" in response.text


def test_a_cancelled_booking_cant_be_edited(client: TestClient) -> None:
    booking = book(client, "10:00", "Tan family")
    client.post(f"/api/bookings/{booking['id']}/cancel")
    page = client.get(f"/bookings/{booking['id']}/edit").text
    assert "Only confirmed bookings can be changed" in page
    assert "Save changes" not in page
    response = client.post(f"/bookings/{booking['id']}/edit", data=edit_form(start="11:00"))
    assert response.status_code == 409


def test_missing_booking(client: TestClient) -> None:
    for url in ("/bookings/nope/edit", "/bookings/nope/cancel"):
        assert client.get(url).status_code == 404
    assert client.post("/bookings/nope/edit", data=edit_form()).status_code == 404
    assert client.post("/bookings/nope/cancel").status_code == 404
    assert client.post("/bookings/nope/no-show").status_code == 404


# --- cancel and no-show ----------------------------------------------------------------------


def test_cancel_asks_first_then_frees_the_zone(client: TestClient) -> None:
    booking = book(client, "10:00", "Tan family")
    page = client.get(f"/bookings/{booking['id']}/cancel").text
    assert "Yes, cancel this booking" in page
    response = client.post(f"/bookings/{booking['id']}/cancel", follow_redirects=False)
    assert response.status_code == 303
    page = client.get(response.headers["location"]).text
    assert "Booking cancelled." in page
    assert "Cancel booking" not in page  # nothing more to do with it
    assert client.get(f"/api/days/{DAY}").json()["bookings"] == []

    again = client.post(f"/bookings/{booking['id']}/cancel")
    assert again.status_code == 409


def test_no_show_only_after_the_start(client: TestClient, database: Database) -> None:
    booking = book(client, "09:00", "Late group", date="2026-11-07")  # today, at 08:00
    early = client.post(f"/bookings/{booking['id']}/no-show")
    assert early.status_code == 409
    assert "Mark a no-show only after the start time" in early.text

    later = TestClient(create_app(database, clock=lambda: Now(today="2026-11-07", minute=600)))
    assert "Mark no-show" in later.get(f"/bookings/{booking['id']}").text
    response = later.post(f"/bookings/{booking['id']}/no-show", follow_redirects=False)
    assert response.status_code == 303
    assert "Marked as no-show." in later.get(response.headers["location"]).text


def test_roles(client: TestClient) -> None:
    booking = book(client, "10:00", "Tan family")
    assert client.get("/bookings", headers=SALES).status_code == 200
    assert (
        client.post(
            f"/bookings/{booking['id']}/cancel", headers=SALES, follow_redirects=False
        ).status_code
        == 303
    )
    other = book(client, "11:00", "Other")
    assert client.post(f"/bookings/{other['id']}/no-show", headers=SALES).status_code == 403
