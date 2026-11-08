"""Booking from the Enquiry check page (Milestone 3, part 2): Book buttons, customer form,
confirmation, and the 'no longer fits' answer."""

from fastapi.testclient import TestClient

HTMX = {"HX-Request": "true"}
REQUEST = "date=2026-12-01&start=09:00&type=PREMIUM&adults=10&children=2"
CUSTOMER = {
    "date": "2026-12-01",
    "start": "09:00",
    "type": "PREMIUM",
    "adults": "10",
    "children": "2",
    "name": "Tan family",
    "phone": "+65 9123 4567",
    "email": "",
    "company": "",
    "notes": "Birthday",
}


def fill_slot(client: TestClient, start: str) -> None:
    """Book all 4 zones on 2026-12-01 at `start` (PREMIUM, 25 each)."""
    for name in ("A", "B", "C", "D"):
        booking = {
            "date": "2026-12-01",
            "start": start,
            "type": "PREMIUM",
            "adults": 25,
            "customer": {"name": f"Group {name}"},
        }
        assert client.post("/api/bookings", json=booking).status_code == 201


def bookings_on(client: TestClient, date: str) -> int:
    """How many bookings there are on `date` (from the day plan)."""
    day = client.get(f"/api/days/{date}").json()
    return len(day["bookings"])


# --- Book buttons on the check result --------------------------------------------------------


def test_a_bookable_request_has_a_book_button(client: TestClient) -> None:
    page = client.get(f"/enquiry?{REQUEST}", headers=HTMX).text
    assert "Good fit" in page
    assert f"/enquiry/book?{REQUEST}" in page.replace("&amp;", "&")


def test_not_possible_has_no_book_button_but_alternatives_do(client: TestClient) -> None:
    fill_slot(client, "10:00")
    page = client.get(
        "/enquiry?date=2026-12-01&start=10:00&type=PREMIUM&adults=10", headers=HTMX
    ).text.replace("&amp;", "&")
    assert "Not possible" in page
    assert "/enquiry/book?date=2026-12-01&start=10:00&" not in page  # not the request itself
    assert page.count("/enquiry/book?") >= 1  # but each alternative
    assert "adults=10&children=0" in page  # alternatives keep the group size


# --- the customer form ------------------------------------------------------------------------


def test_book_button_opens_the_customer_form(client: TestClient) -> None:
    page = client.get(f"/enquiry/book?{REQUEST}", headers=HTMX).text
    assert "<form" in page
    assert 'name="name"' in page
    assert 'value="2026-12-01"' in page  # the enquiry rides along in hidden fields
    assert "Good fit" in page  # verdict for this exact option, checked again
    assert "<html" not in page


def test_book_form_works_without_javascript(client: TestClient) -> None:
    page = client.get(f"/enquiry/book?{REQUEST}").text
    assert "<html" in page
    assert 'name="name"' in page


def test_book_form_for_a_slot_that_is_now_full_shows_the_check_instead(
    client: TestClient,
) -> None:
    fill_slot(client, "10:00")
    page = client.get(
        "/enquiry/book?date=2026-12-01&start=10:00&type=PREMIUM&adults=10", headers=HTMX
    ).text
    assert "Not possible" in page
    assert 'name="name"' not in page


# --- booking ---------------------------------------------------------------------------------


def test_booking_with_htmx_shows_the_confirmation(client: TestClient) -> None:
    response = client.post(
        "/enquiry/book", data=CUSTOMER, headers={**HTMX, "X-User": "sally@jiak99.sg"}
    )
    assert response.status_code == 200
    assert "Booked" in response.text
    assert "Tan family" in response.text
    booking_url = response.headers["HX-Push-Url"]
    assert booking_url.startswith("/bookings/")

    booking = client.get(f"/api{booking_url}").json()
    assert booking["customer"]["name"] == "Tan family"
    assert booking["customer"]["phone"] == "+65 9123 4567"
    assert booking["customer"]["email"] is None  # empty field -> not given
    assert booking["notes"] == "Birthday"
    assert booking["createdBy"] == "sally@jiak99.sg"
    assert (booking["adults"], booking["children"]) == (10, 2)


def test_booking_without_javascript_goes_to_the_booking_page(client: TestClient) -> None:
    response = client.post("/enquiry/book", data=CUSTOMER, follow_redirects=False)
    assert response.status_code == 303
    booking_url = response.headers["location"]

    page = client.get(booking_url)
    assert page.status_code == 200
    assert "Tan family" in page.text
    assert "2026-12-01" in page.text
    assert "09:00" in page.text
    assert "10 adults, 2 children" in page.text


def test_customer_name_is_required(client: TestClient) -> None:
    response = client.post("/enquiry/book", data={**CUSTOMER, "name": "  "}, headers=HTMX)
    assert response.status_code == 422
    assert "Enter the customer" in response.text  # the ' is sent as &#39;
    assert 'name="name"' in response.text  # form shown again, nothing lost
    assert "Birthday" in response.text
    assert bookings_on(client, "2026-12-01") == 0


def test_no_longer_fits_shows_why_and_fresh_alternatives(client: TestClient) -> None:
    fill_slot(client, "09:00")  # someone else took the last zones meanwhile
    response = client.post("/enquiry/book", data=CUSTOMER, headers=HTMX)
    assert response.status_code == 409
    assert "No longer fits" in response.text
    assert "All 4 zones are in use" in response.text
    assert "/enquiry/book?" in response.text  # alternatives can be booked straight away
    assert bookings_on(client, "2026-12-01") == 4  # only the 4 that filled the slot


def test_names_are_shown_safely(client: TestClient) -> None:
    data = {**CUSTOMER, "name": "<script>alert(1)</script>"}
    response = client.post("/enquiry/book", data=data, headers=HTMX)
    assert "<script>alert(1)" not in response.text
    assert "&lt;script&gt;" in response.text


def test_unknown_booking_page_is_404(client: TestClient) -> None:
    response = client.get("/bookings/nope")
    assert response.status_code == 404
    assert "<html" in response.text


def test_htmx_is_told_to_show_409_and_422_answers(client: TestClient) -> None:
    page = client.get("/enquiry").text
    assert 'name="htmx-config"' in page
