"""Availability board, calendar, operations timeline and settings web pages (Milestone 3)."""

from typing import Any

from fastapi.testclient import TestClient

DAY = "2026-12-01"
SALES = {"X-Role": "Sales"}


def book(
    client: TestClient, start: str, ticket_type: str, adults: int, name: str, date: str = DAY
) -> dict[str, Any]:
    body = {
        "date": date,
        "start": start,
        "type": ticket_type,
        "adults": adults,
        "customer": {"name": name},
    }
    response = client.post("/api/bookings", json=body)
    assert response.status_code == 201, response.text
    booking: dict[str, Any] = response.json()
    return booking


# --- navigation ------------------------------------------------------------------------------


def test_every_page_links_to_every_other_page(client: TestClient) -> None:
    page = client.get("/calendar").text
    for link, name in (
        ("/enquiry", "Check and book"),
        ("/ops", "Day plan"),
        ("/calendar", "Calendar"),
        ("/settings", "Settings"),
    ):
        assert f'<a href="{link}">{name}</a>' in page
    assert '<a href="/board">' not in page  # folded into Check and book; /board still works


# --- availability board ----------------------------------------------------------------------


def test_board_shows_free_and_full_slots(client: TestClient) -> None:
    for name in ("A", "B", "C", "D"):
        book(client, "10:00", "PREMIUM", 25, f"Group {name}")
    page = client.get(f"/board?date={DAY}&n=10")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert "Full" in page.text  # 10:00 is taken in every zone
    assert "Free" in page.text  # 09:00-ish and the evening are not
    # A free slot links to the Enquiry check, ready to check.
    assert "/enquiry?date=2026-12-01&amp;start=19:30&amp;type=CLASSIC&amp;adults=10" in page.text


def test_board_defaults_to_today_and_one_person(client: TestClient) -> None:
    page = client.get("/board").text
    assert 'value="2026-11-07"' in page
    assert "What a group of 1 can" in page


def test_board_explains_a_bad_group_size(client: TestClient) -> None:
    page = client.get(f"/board?date={DAY}&n=abc").text
    assert "Group size must be a whole number" in page


def test_board_on_a_closed_day(client: TestClient) -> None:
    response = client.put(f"/api/days/{DAY}/settings", json={"closed": True, "blackouts": []})
    assert response.status_code == 200
    assert "Closed" in client.get(f"/board?date={DAY}").text


# --- calendar --------------------------------------------------------------------------------


def test_calendar_shows_six_months_from_this_month(client: TestClient) -> None:
    page = client.get("/calendar").text
    for title in ("November 2026", "December 2026", "April 2027"):
        assert title in page
    assert "May 2027" not in page
    assert 'class="use0 today"' in page  # 2026-11-07, nothing booked


def test_calendar_colours_a_busy_day_and_shows_locks(client: TestClient) -> None:
    for start in ("09:00", "11:00", "13:00"):
        for name in ("A", "B", "C", "D"):
            book(client, start, "PREMIUM", 25, f"{start} {name}")
    assert client.post(f"/api/days/{DAY}/lock").status_code == 200
    page = client.get("/calendar?start=2026-12").text
    assert "December 2026" in page
    assert "November 2026" not in page
    assert 'class="use3"' in page  # 50% seat-hour use: every zone full 09:00-15:00, of 12 h
    assert "12 bookings, 300 people" in page
    assert "🔒" in page
    assert 'href="/ops?date=2026-12-01"' in page


def test_calendar_ignores_a_bad_start(client: TestClient) -> None:
    assert "November 2026" in client.get("/calendar?start=2026-13").text


# --- operations timeline ---------------------------------------------------------------------


def test_ops_page_draws_each_visit_of_a_route(client: TestClient) -> None:
    book(client, "09:00", "PREMIUM", 18, "Tan family")
    book(client, "12:00", "CLASSIC", 10, "Lim office")
    page = client.get(f"/ops?date={DAY}")
    assert page.status_code == 200
    # Group 1 is drawn once in every zone it visits, labelled with its number.
    for zone in "ABCD":
        assert f"1: PHE · Tan family · 18 people · zone {zone} " in page.text
    assert page.text.count("2: CHE · Lim office · 10 people · zone ") == 2
    assert "(suggested)" in page.text  # not allocated yet
    assert "floating" in page.text
    assert "Seat-hour use" in page.text


def test_ops_page_shades_unsellable_slots(client: TestClient) -> None:
    # SPEC T6: three pinned Premium groups at 19:00 and a Classic leave B 20:00-20:30 and
    # C 20:30-21:00 free, but no ticket except Tingkat can use them.
    for route in ("ABCD", "BCDA", "CDAB"):
        session_id = book(client, "19:00", "PREMIUM", 10, f"Evening {route}")["sessions"][0]["id"]
        assert (
            client.patch(f"/api/sessions/{session_id}/pin", json={"route": route}).status_code
            == 200
        )
    book(client, "19:00", "CLASSIC", 5, "Late Classic")
    page = client.get(f"/ops?date={DAY}").text
    assert "Unsellable 20:00–20:30" in page
    assert "Unsellable 20:30–21:00" in page


def test_allocate_stores_routes_and_comes_back(client: TestClient) -> None:
    book(client, "09:00", "PREMIUM", 18, "Tan family")
    response = client.post(f"/ops/{DAY}/allocate", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/ops?date={DAY}"
    page = client.get(f"/ops?date={DAY}").text
    assert "(suggested)" not in page


def test_lock_and_unlock(client: TestClient) -> None:
    book(client, "09:00", "PREMIUM", 18, "Tan family")
    assert client.post(f"/ops/{DAY}/lock", follow_redirects=False).status_code == 303
    page = client.get(f"/ops?date={DAY}").text
    assert "🔒 Locked" in page
    assert "Unlock day" in page

    again = client.post(f"/ops/{DAY}/lock")  # already locked: refused, page shows why
    assert again.status_code == 409
    assert "already locked" in again.text

    assert client.post(f"/ops/{DAY}/unlock", follow_redirects=False).status_code == 303
    assert "Lock day" in client.get(f"/ops?date={DAY}").text


def test_pin_and_unpin(client: TestClient) -> None:
    booking = book(client, "09:00", "PREMIUM", 18, "Tan family")
    session_id = booking["sessions"][0]["id"]
    response = client.post(
        f"/ops/sessions/{session_id}/pin", data={"route": "dcba "}, follow_redirects=False
    )
    assert response.status_code == 303
    day = client.get(f"/api/days/{DAY}").json()
    assert day["bookings"][0]["sessions"][0]["route"] == "DCBA"
    assert day["bookings"][0]["sessions"][0]["pinned"] is True
    assert "Unpin" in client.get(f"/ops?date={DAY}").text

    client.post(f"/ops/sessions/{session_id}/pin", data={"pinned": "false"})
    day = client.get(f"/api/days/{DAY}").json()
    assert day["bookings"][0]["sessions"][0]["pinned"] is False


def test_pin_into_a_clash_is_refused(client: TestClient) -> None:
    first = book(client, "09:00", "PREMIUM", 18, "First")
    second = book(client, "09:00", "PREMIUM", 10, "Second")
    client.post(f"/ops/sessions/{first['sessions'][0]['id']}/pin", data={"route": "ABCD"})
    response = client.post(
        f"/ops/sessions/{second['sessions'][0]['id']}/pin", data={"route": "ABCD"}
    )
    assert response.status_code == 409
    assert "Route ABCD clashes with a pinned or locked group." in response.text


def test_pin_mistakes(client: TestClient) -> None:
    assert client.post("/ops/sessions/nope/pin", data={"route": "ABCD"}).status_code == 404
    booking = book(client, "09:00", "PREMIUM", 18, "Tan family")
    url = f"/ops/sessions/{booking['sessions'][0]['id']}/pin"
    response = client.post(url, data={"route": "ABCE"})
    assert response.status_code == 422
    assert "zone letters in visiting order" in response.text
    response = client.post(url, data={"route": "AB"})  # a Classic route
    assert response.status_code == 409
    assert "not a route a PHE group can take" in response.text


def test_actions_on_a_bad_date(client: TestClient) -> None:
    response = client.post("/ops/2026-02-30/allocate")
    assert response.status_code == 422
    assert "Pick a real date" in response.text
    assert client.post("/ops/2026-02-30/settings", data={}).status_code == 422


def test_day_settings_blackouts_and_closing(client: TestClient) -> None:
    response = client.post(
        f"/ops/{DAY}/settings", data={"blackouts": "14:00-17:00"}, follow_redirects=False
    )
    assert response.status_code == 303
    page = client.get(f"/ops?date={DAY}").text
    assert "14:00-17:00" in page
    assert "Blocked" in page

    book(client, "09:00", "PREMIUM", 18, "Tan family")
    refused = client.post(f"/ops/{DAY}/settings", data={"closed": "on", "blackouts": ""})
    assert refused.status_code == 409
    assert "has bookings" in refused.text


def test_day_settings_explain_bad_blackouts(client: TestClient) -> None:
    response = client.post(f"/ops/{DAY}/settings", data={"blackouts": "2pm-5pm\n17:00-16:00"})
    assert response.status_code == 422
    assert "is not like 14:00-17:00" in response.text
    assert "the end must be after the start" in response.text
    assert "2pm-5pm" in response.text  # what was typed is kept


def test_sales_cannot_change_the_plan(client: TestClient) -> None:
    assert client.post(f"/ops/{DAY}/allocate", headers=SALES).status_code == 403
    assert client.get(f"/ops?date={DAY}", headers=SALES).status_code == 200


# --- settings --------------------------------------------------------------------------------


def settings_form(**changes: str) -> dict[str, str]:
    form = {
        "turnaroundMinutes": "0",
        "smallGroupWarnBelow": "0",
        "alternativeDaySearch": "7",
        "availabilityCalendarDays": "60",
    }
    for code in ("CLASSIC", "CLASSIC_CT", "PREMIUM", "PREMIUM_CT"):
        form[f"{code}_adult"] = ""
        form[f"{code}_child"] = ""
    form.update(changes)
    return form


def test_settings_page_shows_the_defaults(client: TestClient) -> None:
    page = client.get("/settings").text
    assert 'name="turnaroundMinutes" value="0"' in page
    assert 'name="alternativeDaySearch" value="7"' in page
    assert 'name="PREMIUM_adult" value=""' in page


def test_save_settings_with_prices(client: TestClient) -> None:
    form = settings_form(turnaroundMinutes="15", PREMIUM_adult="98", PREMIUM_child="49.50")
    response = client.post("/settings", data=form, follow_redirects=False)
    assert response.status_code == 303
    saved = client.get("/api/settings").json()
    assert saved["turnaroundMinutes"] == 15
    assert saved["prices"] == {"PREMIUM": {"adultCents": 9800, "childCents": 4950}}
    page = client.get("/settings?saved=1").text
    assert "Saved." in page
    assert 'name="PREMIUM_child" value="49.50"' in page


def test_settings_mistakes_are_explained(client: TestClient) -> None:
    form = settings_form(turnaroundMinutes="500", CLASSIC_adult="10", CLASSIC_child="")
    response = client.post("/settings", data=form)
    assert response.status_code == 422
    assert "Turnaround must be a whole number" in response.text
    assert "CHE: enter both prices" in response.text
    assert 'value="500"' in response.text  # what was typed is kept

    response = client.post("/settings", data=settings_form(PREMIUM_adult="abc", PREMIUM_child="1"))
    assert response.status_code == 422


def test_settings_that_strand_bookings_are_refused(client: TestClient) -> None:
    # Four zones busy 09:00-11:00 and from 11:00: any turnaround makes the day not fit.
    for name in ("A", "B", "C", "D"):
        book(client, "09:00", "PREMIUM", 25, f"Early {name}")
        book(client, "11:00", "PREMIUM", 25, f"Late {name}")
    response = client.post("/settings", data=settings_form(turnaroundMinutes="30"))
    assert response.status_code == 409
    assert "leave no valid plan" in response.text


def test_sales_cannot_change_settings(client: TestClient) -> None:
    assert client.post("/settings", data=settings_form(), headers=SALES).status_code == 403
