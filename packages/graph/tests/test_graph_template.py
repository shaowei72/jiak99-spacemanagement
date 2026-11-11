"""The hand-typed subject template (SPEC §5.4)."""

import pytest

from graph.template import TemplateBooking, parse_template, ticket_from_words


def test_reads_the_template() -> None:
    assert parse_template("[Premium] 11:00 · 10A 2C · Tan family") == TemplateBooking(
        ticket_type="PREMIUM", start=660, adults=10, children=2, customer="Tan family"
    )


def test_children_are_optional_and_case_and_spacing_are_free() -> None:
    assert parse_template("  [tingkat]9:30|8a|  ACME Tours  ") == TemplateBooking(
        ticket_type="TINGKAT", start=570, adults=8, children=0, customer="ACME Tours"
    )


def test_customer_may_contain_separators() -> None:
    booking = parse_template("[classic] 10:00 - 4A - Lim - Ong party")
    assert booking is not None
    assert booking.customer == "Lim - Ong party"


@pytest.mark.parametrize(
    "written",
    ["Classic CT", "classic-ct", "CLASSIC_CT", "classicct", "Classic+CT", "CHE+CT", "che ct"],
)
def test_ways_of_writing_classic_ct(written: str) -> None:
    booking = parse_template(f"[{written}] 12:00 · 5A · X")
    assert booking is not None
    assert booking.ticket_type == "CLASSIC_CT"


@pytest.mark.parametrize(
    ("written", "ticket"),
    [
        ("Tingkat", "TINGKAT"),
        ("CHE", "CLASSIC"),
        ("PHE", "PREMIUM"),
        ("premium ct", "PREMIUM_CT"),
        ("PHE+CT", "PREMIUM_CT"),
        ("deluxe", None),
    ],
)
def test_ticket_words(written: str, ticket: str | None) -> None:
    assert ticket_from_words(written) == ticket


@pytest.mark.parametrize(
    "subject",
    [
        "Tan family 18 pax",  # free text
        "[Deluxe] 11:00 · 10A · Tan",  # unknown ticket
        "[Premium] 25:00 · 10A · Tan",  # no such hour
        "[Premium] 11:60 · 10A · Tan",  # no such minute
        "[Premium] 11:00 · 10 · Tan",  # adults without 'A'
        "[Premium] 11:00 · 10A ·   ",  # no customer
        "[Premium] 11:00 · 10A",  # no customer
    ],
)
def test_not_the_template(subject: str) -> None:
    assert parse_template(subject) is None
