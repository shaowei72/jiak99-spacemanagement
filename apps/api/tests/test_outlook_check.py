"""`uv run outlook-check`: the Outlook connection check (against fakes, never Microsoft)."""

from dataclasses import replace

import pytest

from graph.fake import FakeGateway
from graph.model import CalendarEvent
from graph.real import GraphError
from jiak_api import outlook_check
from jiak_api.clock import Now

REAL_SETTINGS = {
    "GRAPH_MODE": "real",
    "AZURE_TENANT_ID": "t",
    "AZURE_CLIENT_ID": "c",
    "AZURE_CLIENT_SECRET": "s3cret",
    "BOOKINGS_MAILBOX": "jiak99-test@example.com",
}


class OutlookLike(FakeGateway):
    """A fake that, like Outlook, gives every new event an iCalendar UID."""

    def create_event(self, event: CalendarEvent) -> CalendarEvent:
        created = super().create_event(event)
        created = replace(created, ical_uid=f"uid-{created.id}")
        self.events[created.id] = created
        return created


class Forgetful(OutlookLike):
    """Loses the booking id and changes, and never deletes: every check should fail."""

    def get_event(self, event_id: str) -> CalendarEvent:
        return replace(super().get_event(event_id), booking_id="kept", subject="old")

    def list_events(self, first_date: str, last_date: str) -> list[CalendarEvent]:
        return []

    def find_event_by_ical_uid(self, ical_uid: str) -> CalendarEvent | None:
        return None

    def delete_event(self, event_id: str) -> None:
        pass


@pytest.fixture
def real_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REAL_SETTINGS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(outlook_check, "singapore_now", lambda: Now(today="2026-11-13", minute=600))


def use(monkeypatch: pytest.MonkeyPatch, calendar: FakeGateway) -> None:
    monkeypatch.setattr(outlook_check, "make_gateway", lambda config: calendar)


def test_read_only(
    real_settings: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calendar = OutlookLike(
        [CalendarEvent(id="", date="2026-11-14", start=600, end=660, subject="x")]
    )
    use(monkeypatch, calendar)
    assert outlook_check.main([]) == 0
    out = capsys.readouterr().out
    assert "Mailbox: jiak99-test@example.com" in out
    assert "OK       sign in and read the next 7 days (1 events)" in out
    assert calendar.calls == []  # nothing written


def test_write_test_checks_every_operation_and_cleans_up(
    real_settings: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calendar = OutlookLike()
    use(monkeypatch, calendar)
    assert outlook_check.main(["--write-test"]) == 0
    out = capsys.readouterr().out
    assert out.count("OK ") == 8
    assert "All good." in out
    assert calendar.events == {}
    assert [action for action, _ in calendar.calls] == ["create", "update", "update", "delete"]


def test_write_test_reports_each_problem(
    real_settings: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use(monkeypatch, Forgetful())
    assert outlook_check.main(["--write-test"]) == 1
    out = capsys.readouterr().out
    assert (
        "PROBLEM  read it back: subject 'old' (wrote 'Jiak99 connection test (delete me)'); booking_id 'kept'"
        in out
    )
    assert (
        "PROBLEM  find it in the day's list, with its booking id: listed 0 time(s), booking id None"
        in out
    )
    assert (
        "PROBLEM  find it by its iCalendar UID (used by the take-over): UID 'uid-fake-1' found nothing"
        in out
    )
    assert "PROBLEM  remove its booking id: still 'kept'" in out
    assert "PROBLEM  delete it: it is still there" in out
    assert "6 problem(s)" in out


def test_outlook_refuses(
    real_settings: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calendar = OutlookLike()

    def refused(first: str, last: str) -> list[CalendarEvent]:
        raise GraphError("Microsoft sign-in refused the app: AADSTS7000222", 401)

    monkeypatch.setattr(calendar, "list_events", refused)
    use(monkeypatch, calendar)
    assert outlook_check.main([]) == 1
    assert "PROBLEM  talk to Outlook: Microsoft sign-in refused the app" in capsys.readouterr().out


def test_create_without_an_id(
    real_settings: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calendar = OutlookLike()
    original = calendar.create_event

    def no_id(event: CalendarEvent) -> CalendarEvent:
        created = original(event)
        calendar.events[""] = calendar.events.pop(created.id)
        return replace(created, id="")

    monkeypatch.setattr(calendar, "create_event", no_id)
    use(monkeypatch, calendar)
    assert outlook_check.main(["--write-test"]) == 1
    assert "PROBLEM  create a test event: Outlook gave no event id" in capsys.readouterr().out


def test_not_set_up(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("GRAPH_MODE", raising=False)
    assert outlook_check.main([]) == 2
    assert "GRAPH_MODE is not real" in capsys.readouterr().err
    monkeypatch.setenv("GRAPH_MODE", "real")
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    assert outlook_check.main([]) == 2
    assert "GRAPH_MODE=real needs AZURE_TENANT_ID" in capsys.readouterr().err
