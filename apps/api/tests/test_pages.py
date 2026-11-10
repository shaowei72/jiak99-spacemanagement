"""The Check and book web page (Milestone 3)."""

from fastapi.testclient import TestClient

HTMX = {"HX-Request": "true"}


def test_home_goes_to_the_enquiry_check(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/enquiry"


def test_empty_page_shows_the_form_with_today_and_all_ticket_types(client: TestClient) -> None:
    response = client.get("/enquiry")
    assert response.status_code == 200
    assert 'value="2026-11-07"' in response.text  # today, from the test clock
    for label in ("CHE – Classic", "CHE+CT", "PHE – Premium", "PHE+CT"):
        assert label in response.text
    assert "Good fit" not in response.text  # no verdict before the form is sent


def test_a_free_slot_is_a_good_fit(client: TestClient) -> None:
    query = "date=2026-12-01&start=09:00&type=PREMIUM&adults=10&children=0"
    response = client.get(f"/enquiry?{query}")
    assert response.status_code == 200
    assert "Good fit" in response.text
    assert "<form" in response.text  # whole page, form kept
    assert 'value="09:00"' in response.text
    assert response.headers["cache-control"] == "no-store"


def test_htmx_gets_only_the_result_part(client: TestClient) -> None:
    query = "date=2026-12-01&start=09:00&type=PREMIUM&adults=10"
    response = client.get(f"/enquiry?{query}", headers=HTMX)
    assert "Good fit" in response.text
    assert "<form" not in response.text
    assert "<html" not in response.text


def test_page_and_json_api_give_the_same_reason(client: TestClient) -> None:
    query = "date=2026-12-01&start=10:00&type=PREMIUM&adults=10"
    reason = client.get(f"/api/enquiries/check?{query}").json()["reason"]
    page = client.get(f"/enquiry?{query}", headers=HTMX).text
    assert reason in page


def test_a_big_group_shows_its_split(client: TestClient) -> None:
    query = "date=2026-12-01&start=10:00&type=PREMIUM&adults=30"
    page = client.get(f"/enquiry?{query}", headers=HTMX).text
    assert "Split into 2 groups:" in page
    assert "15 + 15 people." in page


def test_a_full_slot_shows_not_possible_with_alternatives(client: TestClient) -> None:
    for name in ("A", "B", "C", "D"):
        booking = {
            "date": "2026-12-01",
            "start": "10:00",
            "type": "PREMIUM",
            "adults": 25,
            "customer": {"name": f"Group {name}"},
        }
        assert client.post("/api/bookings", json=booking).status_code == 201
    query = "date=2026-12-01&start=10:00&type=PREMIUM&adults=10"
    page = client.get(f"/enquiry?{query}", headers=HTMX).text
    assert "Not possible" in page
    assert "Alternatives" in page


def test_form_mistakes_are_explained(client: TestClient) -> None:
    page = client.get("/enquiry?date=2026-02-30&start=abc&type=PREMIUM&adults=x", headers=HTMX).text
    assert "Pick a start time" in page
    assert "Adults must be a whole number" in page

    page = client.get("/enquiry?date=2026-02-30&start=10:00&type=PREMIUM&adults=2", headers=HTMX)
    assert "Pick a date." in page.text

    page = client.get("/enquiry?date=2026-12-01&start=10:00&type=PREMIUM&adults=0", headers=HTMX)
    assert "at least one person" in page.text


def test_sales_may_use_the_page(client: TestClient) -> None:
    response = client.get("/enquiry", headers={"X-Role": "Sales"})
    assert response.status_code == 200


def test_styles_and_htmx_are_served(client: TestClient) -> None:
    assert client.get("/static/app.css").status_code == 200
    assert "htmx" in client.get("/static/vendor/htmx.min.js").text


# --- times that fit (the availability board, folded into Check and book) ---------------------


def fill_morning(client: TestClient) -> None:
    """Every zone slot taken 09:00-11:00 on 2026-12-01 (four Premium groups)."""
    for name in ("A", "B", "C", "D"):
        booking = {
            "date": "2026-12-01",
            "start": "09:00",
            "type": "PREMIUM",
            "adults": 25,
            "customer": {"name": f"Morning {name}"},
        }
        assert client.post("/api/bookings", json=booking).status_code == 201


def test_page_is_called_check_and_book(client: TestClient) -> None:
    page = client.get("/enquiry").text
    assert "<h1>Check and book</h1>" in page
    assert 'href="/board"' not in page  # the board is part of this page now


def test_result_lists_the_other_times_that_fit(client: TestClient) -> None:
    fill_morning(client)
    query = "date=2026-12-01&start=10:00&type=PREMIUM&adults=10"
    page = client.get(f"/enquiry?{query}", headers=HTMX).text
    assert "Not possible" in page
    assert "Times that fit on Tue 2026-12-01 for 10" in page
    panel = page[page.index("Times that fit") :]
    assert ">10:30<" not in panel  # every zone still busy until 11:00
    assert ">11:00<" in panel
    assert ">19:00<" in panel  # last PHE start
    assert ">20:30<" in panel  # only for Tingkat
    # The asked ticket comes first, and a time links to its check.
    assert panel.index("PHE <span") < panel.index("CHE")
    assert "/enquiry?date=2026-12-01&amp;start=11:00&amp;type=PREMIUM&amp;adults=10" in panel


def test_without_a_start_time_only_the_times_are_shown(client: TestClient) -> None:
    query = "date=2026-12-01&start=&type=CLASSIC&adults=40&children=2"
    page = client.get(f"/enquiry?{query}", headers=HTMX).text
    assert "Times that fit on Tue 2026-12-01 for 42" in page
    assert "Good fit" not in page
    assert "Not possible" not in page
    assert "Pick a start time" not in page


def test_the_asked_time_is_highlighted(client: TestClient) -> None:
    page = client.get("/enquiry?date=2026-12-01&start=14:00&type=PREMIUM&adults=5").text
    assert 'class="chip current"' in page
    assert 'value="14:00"' in page


def test_a_full_day_and_a_closed_day(client: TestClient) -> None:
    # Four Premium groups every two hours fill every zone slot from 09:00 to 21:00.
    for name in ("A", "B", "C", "D"):
        for start in ("09:00", "11:00", "13:00", "15:00", "17:00", "19:00"):
            booking = {
                "date": "2026-12-01",
                "start": start,
                "type": "PREMIUM",
                "adults": 25,
                "customer": {"name": f"{name} {start}"},
            }
            assert client.post("/api/bookings", json=booking).status_code == 201
    page = client.get("/enquiry?date=2026-12-01&start=&type=PREMIUM&adults=5", headers=HTMX).text
    assert "Nothing fits this group on this day." in page

    client.put("/api/days/2026-12-02/settings", json={"closed": True, "blackouts": []})
    page = client.get("/enquiry?date=2026-12-02&start=&type=PREMIUM&adults=5", headers=HTMX).text
    assert "Closed on this date." in page


def test_no_times_when_the_form_has_mistakes(client: TestClient) -> None:
    page = client.get("/enquiry?date=2026-12-01&start=&type=PREMIUM&adults=0", headers=HTMX).text
    assert "at least one person" in page
    assert "Times that fit" not in page
