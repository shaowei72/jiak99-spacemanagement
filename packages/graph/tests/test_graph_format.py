"""Subject and body the app writes for a booking (SPEC §5.4)."""

from dataclasses import replace

from graph.format import (
    BookingInfo,
    booking_body,
    booking_event,
    booking_subject,
    routes_text,
    ticket_category,
)

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


def test_subject_matches_the_spec_example() -> None:
    assert booking_subject(TAN_FAMILY) == "PHE+CT · Tan family · 18 pax · ABDC · 11:00–14:00"


def test_split_group_lists_every_route() -> None:
    info = replace(
        TAN_FAMILY,
        ticket_type="PREMIUM",
        customer="Lee wedding",
        adults=40,
        children=0,
        start=600,
        routes=("ABCD", "DCBA"),
    )
    assert booking_subject(info) == "PHE · Lee wedding · 40 pax · ABCD/DCBA · 10:00–12:00"


def test_route_left_out_until_every_group_has_one() -> None:
    info = replace(TAN_FAMILY, routes=("ABDC", None))
    assert routes_text(info) == ""
    assert booking_subject(info) == "PHE+CT · Tan family · 18 pax · 11:00–14:00"
    assert "Route:" not in booking_body(info)


def test_body_has_the_fixed_layout() -> None:
    info = replace(TAN_FAMILY, contact="Mrs Tan, 9123 4567", notes="  Halal set for 2  ")
    assert booking_body(info) == (
        "Booking: b123   Ticket: Premium Hawker Experience + Chef's Table   Status: confirmed\n"
        "Customer: Tan family   Contact: Mrs Tan, 9123 4567\n"
        "People: 18 (15 adults, 3 children)   Route: ABDC   Times: 11:00–14:00\n"
        "Notes: Halal set for 2"
    )


def test_body_leaves_out_empty_parts() -> None:
    info = replace(TAN_FAMILY, children=0, notes="   ")
    assert booking_body(info) == (
        "Booking: b123   Ticket: Premium Hawker Experience + Chef's Table   Status: confirmed\n"
        "Customer: Tan family\n"
        "People: 15   Route: ABDC   Times: 11:00–14:00"
    )


def test_event_carries_booking_id_category_and_times() -> None:
    info = replace(TAN_FAMILY, ticket_type="TINGKAT")
    event = booking_event(info, event_id="ev-1", created_by="sales@example.com")
    assert event.id == "ev-1"
    assert event.booking_id == "b123"
    assert (event.date, event.start, event.end) == ("2026-12-01", 660, 690)
    assert event.categories == (ticket_category("TINGKAT"),) == ("Tingkat",)
    assert event.created_by == "sales@example.com"
    assert event.subject.startswith("Tingkat · Tan family")
