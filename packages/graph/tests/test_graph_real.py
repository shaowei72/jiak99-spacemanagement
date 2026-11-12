"""The real Microsoft Graph gateway, against a pretend Microsoft (no network)."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from graph.gateway import EventNotFound, GraphGateway
from graph.model import CalendarEvent
from graph.real import (
    EXTENSION_ID,
    GraphCalendar,
    GraphError,
    GraphSettings,
    event_from_graph,
    event_to_graph,
    graph_time,
    read_graph_time,
)

SETTINGS = GraphSettings(
    tenant_id="tenant-1", client_id="client-1", client_secret="s3cret", mailbox="Test@Example.com"
)
EVENTS = "https://graph.microsoft.com/v1.0/users/Test%40Example.com"
SGT = "Singapore Standard Time"

Handler = Callable[[httpx.Request], httpx.Response]


def sgt(text: str) -> dict[str, str]:
    return {"dateTime": text + ".0000000", "timeZone": SGT}


def graph_event(**changes: Any) -> dict[str, Any]:
    """An event as Graph returns it."""
    data: dict[str, Any] = {
        "id": "AAMk-1",
        "subject": "PHE · Tan family · 18 pax · 11:00–13:00",
        "body": {"contentType": "text", "content": "Booking: 7\r\nNotes: none\r\n"},
        "start": sgt("2026-11-20T11:00:00"),
        "end": sgt("2026-11-20T13:00:00"),
        "isAllDay": False,
        "isCancelled": False,
        "categories": ["PHE"],
        "organizer": {"emailAddress": {"name": "Ops", "address": "ops@example.com"}},
        "iCalUId": "uid-1",
        "createdDateTime": "2026-11-11T09:30:00Z",
        "extensions": [{"id": EXTENSION_ID, "bookingId": "7"}],
    }
    data.update(changes)
    return data


class PretendMicrosoft:
    """Answers sign-in itself; every Graph request goes to `graph` (set by each test).
    `requests` keeps every request made, `waits` every pause the gateway asked for."""

    def __init__(self, graph: Handler | None = None) -> None:
        self.graph: Handler = graph or (lambda request: httpx.Response(200, json={}))
        self.requests: list[httpx.Request] = []
        self.sign_ins = 0
        self.sign_in_answer: httpx.Response | None = None
        self.waits: list[float] = []
        self.now = 1000.0

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "login.microsoftonline.com":
            self.sign_ins += 1
            if self.sign_in_answer is not None:
                return self.sign_in_answer
            return httpx.Response(
                200, json={"access_token": f"token-{self.sign_ins}", "expires_in": 3600}
            )
        return self.graph(request)

    def calendar(self) -> GraphCalendar:
        client = httpx.Client(transport=httpx.MockTransport(self.handle))
        return GraphCalendar(SETTINGS, client, sleep=self.waits.append, clock=lambda: self.now)

    def graph_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == "graph.microsoft.com"]


def body_of(request: httpx.Request) -> Any:
    return json.loads(request.content)


def answer(status: int, data: Any = None, **headers: str) -> httpx.Response:
    return httpx.Response(status, json=data if data is not None else {}, headers=headers)


# --- converting ---------------------------------------------------------------------------------


def test_times_in_and_out() -> None:
    assert graph_time("2026-11-20", 600) == {"dateTime": "2026-11-20T10:00:00", "timeZone": SGT}
    assert graph_time("2026-11-30", 1500)["dateTime"] == "2026-12-01T01:00:00"
    assert read_graph_time(sgt("2026-11-20T10:30:00")) == ("2026-11-20", 630)
    with pytest.raises(GraphError, match="'UTC'"):
        read_graph_time({"dateTime": "2026-11-20T02:30:00", "timeZone": "UTC"})


def test_event_from_graph() -> None:
    assert event_from_graph(graph_event()) == CalendarEvent(
        id="AAMk-1",
        date="2026-11-20",
        start=660,
        end=780,
        subject="PHE · Tan family · 18 pax · 11:00–13:00",
        body="Booking: 7\nNotes: none",
        categories=("PHE",),
        booking_id="7",
        created_by="ops@example.com",
        ical_uid="uid-1",
        created="2026-11-11T09:30:00Z",
    )


def test_event_from_graph_with_little_in_it() -> None:
    event = event_from_graph(
        {
            "id": "AAMk-2",
            "start": sgt("2026-11-20T00:00:00"),
            "end": sgt("2026-11-22T00:00:00"),
            "isAllDay": True,
            "body": None,
            "organizer": None,
            "extensions": [{"id": "Other.Extension", "bookingId": "9"}, {"id": EXTENSION_ID}],
        }
    )
    assert (event.start, event.end, event.all_day) == (0, 2880, True)
    assert (event.subject, event.body, event.created_by, event.booking_id) == ("", "", "", None)
    assert (event.categories, event.ical_uid, event.created) == ((), "", "")


def test_event_to_graph() -> None:
    event = CalendarEvent(
        id="x", date="2026-11-20", start=1260, end=1500, subject="S", body="B", categories=("CHE",)
    )
    assert event_to_graph(event) == {
        "subject": "S",
        "body": {"contentType": "text", "content": "B"},
        "start": {"dateTime": "2026-11-20T21:00:00", "timeZone": SGT},
        "end": {"dateTime": "2026-11-21T01:00:00", "timeZone": SGT},
        "isAllDay": False,
        "categories": ["CHE"],
        "showAs": "busy",
    }


def test_the_secret_is_not_shown() -> None:
    assert "s3cret" not in repr(SETTINGS)


def test_it_follows_the_gateway_protocol() -> None:
    calendar: GraphGateway = PretendMicrosoft().calendar()
    assert calendar.calendar_id() == "graph:test@example.com"
    assert GraphCalendar(SETTINGS).calendar_id() == "graph:test@example.com"  # own client


# --- signing in ---------------------------------------------------------------------------------


def test_sign_in_once_and_again_when_the_token_runs_out() -> None:
    ms = PretendMicrosoft(lambda request: answer(204))
    calendar = ms.calendar()
    calendar.delete_event("a")
    calendar.delete_event("b")
    assert ms.sign_ins == 1
    sign_in = ms.requests[0]
    assert sign_in.url.path == "/tenant-1/oauth2/v2.0/token"
    form = dict(httpx.QueryParams(sign_in.content.decode()))
    assert form == {
        "grant_type": "client_credentials",
        "client_id": "client-1",
        "client_secret": "s3cret",
        "scope": "https://graph.microsoft.com/.default",
    }
    first = ms.graph_requests()[0]
    assert first.headers["Authorization"] == "Bearer token-1"
    assert first.headers["Prefer"] == (
        'outlook.timezone="Singapore Standard Time", outlook.body-content-type="text"'
    )

    ms.now += 3600 - 300  # 5 minutes before it runs out: a new one
    calendar.delete_event("c")
    assert ms.sign_ins == 2
    assert ms.graph_requests()[-1].headers["Authorization"] == "Bearer token-2"


def test_sign_in_refused() -> None:
    ms = PretendMicrosoft()
    ms.sign_in_answer = answer(
        401,
        {
            "error": "invalid_client",
            "error_description": "AADSTS7000222: The provided client secret keys are expired.\r\nTrace ID: 1",
        },
    )
    with pytest.raises(GraphError) as caught:
        ms.calendar().delete_event("a")
    assert str(caught.value) == (
        "Microsoft sign-in refused the app: AADSTS7000222: The provided client secret keys are "
        "expired.. Check AZURE_TENANT_ID, AZURE_CLIENT_ID and AZURE_CLIENT_SECRET (has the secret "
        "expired?)."
    )
    assert caught.value.status == 401


def test_a_withdrawn_token_signs_in_once_more() -> None:
    answers = iter([answer(401), answer(204)])
    ms = PretendMicrosoft(lambda request: next(answers))
    ms.calendar().delete_event("a")
    assert ms.sign_ins == 2

    ms = PretendMicrosoft(
        lambda request: answer(
            401, {"error": {"code": "InvalidAuthenticationToken", "message": "Expired"}}
        )
    )
    with pytest.raises(GraphError, match="InvalidAuthenticationToken: Expired"):
        ms.calendar().delete_event("a")
    assert ms.sign_ins == 2


def test_no_network() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no internet")

    client = httpx.Client(transport=httpx.MockTransport(broken))
    with pytest.raises(GraphError, match="Can't reach Microsoft sign-in: no internet"):
        GraphCalendar(SETTINGS, client).delete_event("a")

    ms = PretendMicrosoft(broken)
    with pytest.raises(GraphError, match="Can't reach Microsoft Graph: no internet"):
        ms.calendar().delete_event("a")


# --- busy and refused ---------------------------------------------------------------------------


def test_busy_waits_and_tries_again() -> None:
    answers = iter([answer(429, **{"Retry-After": "5"}), answer(503), answer(204)])
    ms = PretendMicrosoft(lambda request: next(answers))
    ms.calendar().delete_event("a")
    assert ms.waits == [5.0, 2.0]


def test_busy_three_times_gives_up() -> None:
    ms = PretendMicrosoft(lambda request: answer(504, **{"Retry-After": "600"}))
    with pytest.raises(GraphError, match="HTTP 504"):
        ms.calendar().delete_event("a")
    assert ms.waits == [30.0, 30.0]
    assert len(ms.graph_requests()) == 3

    ms = PretendMicrosoft(lambda request: answer(429, **{"Retry-After": "soon"}))
    with pytest.raises(GraphError):
        ms.calendar().delete_event("a")
    assert ms.waits == [2.0, 2.0]


def test_refused_requests_say_what_to_check() -> None:
    ms = PretendMicrosoft(
        lambda request: answer(
            403, {"error": {"code": "ErrorAccessDenied", "message": "Access is denied."}}
        )
    )
    with pytest.raises(GraphError) as caught:
        ms.calendar().delete_event("a")
    assert str(caught.value).startswith("Outlook: ErrorAccessDenied: Access is denied.. The app")
    assert "may not be allowed to reach Test@Example.com" in str(caught.value)

    ms = PretendMicrosoft(
        lambda request: answer(
            404, {"error": {"code": "MailboxNotEnabledForRESTAPI", "message": "No."}}
        )
    )
    with pytest.raises(GraphError, match=r"Is BOOKINGS_MAILBOX \(Test@Example.com\) right\?"):
        ms.calendar().list_events("2026-11-20", "2026-11-20")

    ms = PretendMicrosoft(lambda request: httpx.Response(500, text="<html>oops</html>"))
    with pytest.raises(GraphError, match="Outlook: HTTP 500"):
        ms.calendar().list_events("2026-11-20", "2026-11-20")

    ms = PretendMicrosoft(lambda request: answer(400, ["odd"]))
    with pytest.raises(GraphError, match="Outlook: HTTP 400"):
        ms.calendar().list_events("2026-11-20", "2026-11-20")


# --- reading ------------------------------------------------------------------------------------


def test_list_events_reads_every_page() -> None:
    next_page = f"{EVENTS}/calendar/calendarView?$skip=2"
    pages = {
        "first": {
            "value": [
                graph_event(
                    id="late", start=sgt("2026-11-21T15:00:00"), end=sgt("2026-11-21T16:00:00")
                ),
                graph_event(id="cancelled", isCancelled=True),
                graph_event(
                    id="day-before",
                    start=sgt("2026-11-19T23:00:00"),
                    end=sgt("2026-11-20T01:00:00"),
                ),
            ],
            "@odata.nextLink": next_page,
        },
        "second": {"value": [graph_event(id="early")]},
    }

    def graph(request: httpx.Request) -> httpx.Response:
        return answer(200, pages["second" if "skip" in str(request.url) else "first"])

    ms = PretendMicrosoft(graph)
    events = ms.calendar().list_events("2026-11-20", "2026-11-21")
    assert [e.id for e in events] == ["early", "late"]

    first, second = ms.graph_requests()
    assert first.url.path == "/v1.0/users/Test@Example.com/calendar/calendarView"
    assert first.url.params["startDateTime"] == "2026-11-20T00:00:00+08:00"
    assert first.url.params["endDateTime"] == "2026-11-22T00:00:00+08:00"
    assert first.url.params["$expand"] == f"extensions($filter=id eq '{EXTENSION_ID}')"
    assert first.url.params["$top"] == "100"
    assert "startDateTime" not in second.url.params  # the next-page link has them


def test_get_event() -> None:
    ms = PretendMicrosoft(lambda request: answer(200, graph_event()))
    assert ms.calendar().get_event("AAMk/1=").booking_id == "7"
    assert (
        ms.graph_requests()[0].url.raw_path.split(b"?")[0]
        == b"/v1.0/users/Test%40Example.com/events/AAMk%2F1%3D"
    )

    ms = PretendMicrosoft(
        lambda request: answer(404, {"error": {"code": "ErrorItemNotFound", "message": "Gone"}})
    )
    with pytest.raises(EventNotFound):
        ms.calendar().get_event("AAMk-1")

    ms = PretendMicrosoft(lambda request: answer(500))
    with pytest.raises(GraphError):
        ms.calendar().get_event("AAMk-1")


def test_find_event_by_ical_uid() -> None:
    ms = PretendMicrosoft(lambda request: answer(200, {"value": [graph_event()]}))
    calendar = ms.calendar()
    found = calendar.find_event_by_ical_uid("it's-1")
    assert found is not None and found.id == "AAMk-1"
    assert ms.graph_requests()[0].url.params["$filter"] == "iCalUId eq 'it''s-1'"

    ms.graph = lambda request: answer(200, {"value": []})
    assert calendar.find_event_by_ical_uid("uid-2") is None
    count = len(ms.requests)
    assert calendar.find_event_by_ical_uid("") is None
    assert len(ms.requests) == count  # nothing asked


# --- writing ------------------------------------------------------------------------------------

NEW = CalendarEvent(id="", date="2026-11-20", start=660, end=780, subject="S", body="B")


def test_create_event() -> None:
    created = {"id": "AAMk-9", "iCalUId": "uid-9", "createdDateTime": "2026-11-13T02:00:00Z"}
    ms = PretendMicrosoft(lambda request: answer(201, created))
    calendar = ms.calendar()
    result = calendar.create_event(CalendarEvent(**{**NEW.__dict__, "booking_id": "7"}))
    assert (result.id, result.ical_uid, result.created, result.booking_id) == (
        "AAMk-9",
        "uid-9",
        "2026-11-13T02:00:00Z",
        "7",
    )
    request = ms.graph_requests()[0]
    assert (request.method, request.url.path) == (
        "POST",
        "/v1.0/users/Test@Example.com/calendar/events",
    )
    assert body_of(request)["extensions"] == [
        {
            "@odata.type": "microsoft.graph.openTypeExtension",
            "extensionName": "com.jiak99.booking",
            "bookingId": "7",
        }
    ]

    ms.graph = lambda request: answer(201, {"id": "AAMk-10"})
    result = calendar.create_event(NEW)
    assert (result.id, result.ical_uid, result.created) == ("AAMk-10", "", "")
    assert "extensions" not in body_of(ms.graph_requests()[-1])


def test_update_event_writes_the_booking_id() -> None:
    answers = iter([answer(200), answer(404), answer(201)])  # event, extension missing, added
    ms = PretendMicrosoft(lambda request: next(answers))
    event = CalendarEvent(**{**NEW.__dict__, "id": "AAMk-1", "booking_id": "7"})
    assert ms.calendar().update_event(event) == event
    patch, patch_extension, post_extension = ms.graph_requests()
    assert (patch.method, patch.url.path) == (
        "PATCH",
        "/v1.0/users/Test@Example.com/events/AAMk-1",
    )
    assert body_of(patch)["subject"] == "S"
    assert (patch_extension.method, patch_extension.url.path) == (
        "PATCH",
        "/v1.0/users/Test@Example.com/events/AAMk-1/extensions/com.jiak99.booking",
    )
    assert (post_extension.method, post_extension.url.path) == (
        "POST",
        "/v1.0/users/Test@Example.com/events/AAMk-1/extensions",
    )
    assert body_of(post_extension)["bookingId"] == "7"


def test_update_event_without_booking_id_removes_the_extension() -> None:
    answers = iter([answer(200), answer(404)])  # event, no extension to remove
    ms = PretendMicrosoft(lambda request: next(answers))
    ms.calendar().update_event(CalendarEvent(**{**NEW.__dict__, "id": "AAMk-1"}))
    assert [r.method for r in ms.graph_requests()] == ["PATCH", "DELETE"]


def test_update_event_problems() -> None:
    ms = PretendMicrosoft(lambda request: answer(404))
    with pytest.raises(EventNotFound):
        ms.calendar().update_event(CalendarEvent(**{**NEW.__dict__, "id": "AAMk-1"}))

    ms = PretendMicrosoft(lambda request: answer(500))
    with pytest.raises(GraphError):
        ms.calendar().update_event(CalendarEvent(**{**NEW.__dict__, "id": "AAMk-1"}))

    answers = iter([answer(200), answer(403)])
    ms = PretendMicrosoft(lambda request: next(answers))
    with pytest.raises(GraphError):
        ms.calendar().update_event(CalendarEvent(**{**NEW.__dict__, "id": "AAMk-1"}))


def test_delete_event() -> None:
    ms = PretendMicrosoft(lambda request: answer(204))
    ms.calendar().delete_event("AAMk-1")
    assert ms.graph_requests()[0].method == "DELETE"

    ms = PretendMicrosoft(lambda request: answer(404))
    with pytest.raises(EventNotFound):
        ms.calendar().delete_event("AAMk-1")

    ms = PretendMicrosoft(lambda request: answer(500))
    with pytest.raises(GraphError):
        ms.calendar().delete_event("AAMk-1")
