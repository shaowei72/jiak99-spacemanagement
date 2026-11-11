"""Notes from the app, text kept from before the app, and reading what people changed in Outlook
(SPEC §5.4)."""

import pytest

from graph.format import (
    DIVIDER,
    BookingInfo,
    booking_body,
    booking_event,
    compose_body,
    kept_text,
    read_booking_subject,
    split_body,
    visible_state,
)
from graph.model import CalendarEvent

TAN_FAMILY = BookingInfo(
    id="b123",
    date="2026-12-01",
    start=660,
    ticket_type="PREMIUM_CT",
    customer="Tan family",
    adults=15,
    children=3,
    routes=("ABDC",),
)
OLD_TEXT = "---------- Forwarded message ----------\nMenu: set B; 2 halal\n\nThanks!"


def event(body: str, booking_id: str | None = None) -> CalendarEvent:
    return CalendarEvent(
        id="e1",
        date="2026-12-01",
        start=660,
        end=840,
        subject="x",
        body=body,
        booking_id=booking_id,
    )


def test_compose_and_split_give_back_the_kept_text_exactly() -> None:
    body = compose_body("Booking: b123", OLD_TEXT)
    assert body == f"Booking: b123\n\n{DIVIDER}\n{OLD_TEXT}"
    assert split_body(body) == ("Booking: b123", OLD_TEXT)


def test_no_divider_means_no_kept_text() -> None:
    assert compose_body("Booking: b123", None) == "Booking: b123"
    assert split_body("Booking: b123") == ("Booking: b123", None)


def test_kept_text_of_an_event() -> None:
    # Not taken over yet: the whole old body is kept.
    assert kept_text(event(OLD_TEXT)) == OLD_TEXT
    # Taken over before: only what is below the divider.
    assert kept_text(event(compose_body("Booking: b123", OLD_TEXT), "b123")) == OLD_TEXT
    # The app's own event: nothing to keep.
    assert kept_text(event("Booking: b123", "b123")) is None
    # An empty body: nothing to keep.
    assert kept_text(event("  ")) is None


def test_booking_event_with_a_note_and_kept_text() -> None:
    made = booking_event(TAN_FAMILY, note="Change not made: no room.", kept=OLD_TEXT)
    assert made.body == (
        f"Change not made: no room.\n\n{booking_body(TAN_FAMILY)}\n\n{DIVIDER}\n{OLD_TEXT}"
    )
    assert made.categories == ("PHE+CT", "Conflict")
    plain = booking_event(TAN_FAMILY)
    assert plain.body == booking_body(TAN_FAMILY)
    assert plain.categories == ("PHE+CT",)


def test_visible_state_is_date_times_and_subject() -> None:
    made = booking_event(TAN_FAMILY)
    assert (
        visible_state(made)
        == "2026-12-01 660-840 PHE+CT · Tan family · 18 pax · ABDC · 11:00–14:00"
    )


@pytest.mark.parametrize(
    ("subject", "result"),
    [
        ("PHE+CT · Tan family · 18 pax · ABDC · 11:00–14:00", ("PREMIUM_CT", 18)),
        ("CHE · Ong party · 20 PAX · 10:00–11:00", ("CLASSIC", 20)),
        ("Tingkat·Lim·6pax", ("TINGKAT", 6)),
        ("PHE+CT · Tan family · lots · 11:00–14:00", None),
        ("VIP · Tan family · 18 pax", None),
        ("PHE · 18 pax", None),
        ("typed over", None),
    ],
)
def test_read_booking_subject(subject: str, result: tuple[str, int] | None) -> None:
    assert read_booking_subject(subject) == result
