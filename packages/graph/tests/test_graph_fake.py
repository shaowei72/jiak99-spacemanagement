"""The in-memory calendar used for development and tests."""

from dataclasses import replace

import pytest

from graph.fake import FakeGateway
from graph.gateway import EventNotFound, GraphGateway
from graph.model import CalendarEvent


def event(date: str, start: int, subject: str, event_id: str = "") -> CalendarEvent:
    return CalendarEvent(id=event_id, date=date, start=start, end=start + 60, subject=subject)


def test_starting_events_keep_or_get_ids_and_are_not_counted_as_calls() -> None:
    gateway = FakeGateway(
        [event("2026-12-01", 600, "kept", "outlook-9"), event("2026-12-01", 540, "new")]
    )
    assert set(gateway.events) == {"outlook-9", "fake-1"}
    assert gateway.calls == []


def test_list_is_sorted_and_limited_to_the_dates() -> None:
    gateway = FakeGateway(
        [
            event("2026-12-02", 540, "later day"),
            event("2026-12-01", 720, "noon"),
            event("2026-12-01", 540, "morning"),
            event("2026-12-03", 540, "outside"),
        ]
    )
    subjects = [e.subject for e in gateway.list_events("2026-12-01", "2026-12-02")]
    assert subjects == ["morning", "noon", "later day"]


def test_create_update_delete_are_recorded() -> None:
    fake = FakeGateway()
    gateway: GraphGateway = fake  # the fake follows the protocol
    created = gateway.create_event(event("2026-12-01", 600, "first", "ignored"))
    assert created.id == "fake-1"
    assert gateway.get_event("fake-1") == created

    changed = gateway.update_event(replace(created, subject="second"))
    assert gateway.get_event("fake-1").subject == "second" == changed.subject

    gateway.delete_event("fake-1")
    assert fake.calls == [("create", "fake-1"), ("update", "fake-1"), ("delete", "fake-1")]
    assert fake.events == {}


def test_unknown_ids_raise_event_not_found() -> None:
    gateway = FakeGateway()
    with pytest.raises(EventNotFound):
        gateway.get_event("nope")
    with pytest.raises(EventNotFound):
        gateway.update_event(event("2026-12-01", 600, "x", "nope"))
    with pytest.raises(EventNotFound):
        gateway.delete_event("nope")


def test_find_by_ical_uid() -> None:
    with_uid = replace(event("2026-12-01", 600, "old subject"), ical_uid="040000008200E001")
    gateway = FakeGateway([event("2026-12-01", 540, "other"), with_uid])
    found = gateway.find_event_by_ical_uid("040000008200E001")
    assert found is not None
    assert found.subject == "old subject"
    assert gateway.find_event_by_ical_uid("unknown") is None
    assert gateway.find_event_by_ical_uid("") is None  # events without a UID never match


def test_each_fake_is_a_different_calendar_and_events_get_a_created_order() -> None:
    first, second = FakeGateway(), FakeGateway()
    assert first.calendar_id() != second.calendar_id()
    assert first.calendar_id() == first.calendar_id()

    a = first.create_event(event("2026-12-01", 600, "a"))
    b = first.create_event(event("2026-12-01", 540, "b"))
    given = first.create_event(
        replace(event("2026-12-01", 540, "c"), created="2026-11-11T09:30:00Z")
    )
    assert a.created < b.created
    assert given.created == "2026-11-11T09:30:00Z"
