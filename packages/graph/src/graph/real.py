"""The real Bookings calendar, through Microsoft Graph (SPEC §5.4). Used only with GRAPH_MODE=real.

It follows the same `GraphGateway` protocol as the fake, so the rest of the app doesn't know which
one it has. Plain HTTPS calls (with `httpx`), not Microsoft's SDK: the few calls the app needs are
easier to read this way, and tests replay Microsoft's answers without any network.

How it talks to Microsoft:
- Sign-in: the app's own ID and secret (the M365 admin's app registration, README.md) get an access
  token, which is reused until 5 minutes before it runs out.
- Times: Outlook is asked to give and take times in Singapore time, so the app's 'YYYY-MM-DD' dates
  and minutes since midnight map straight onto Outlook's times.
- Bodies are read and written as plain text.
- The app's booking id is kept in an open extension called `com.jiak99.booking` on the event.
- When Microsoft says it is busy (429 / 503 / 504) it waits as asked and tries again, up to 3 times.

Every other problem raises `GraphError` with a message saying what to check.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import quote

import httpx

from engine.dates import add_days, days_between
from graph.gateway import EventNotFound
from graph.model import CalendarEvent

GRAPH_URL = "https://graph.microsoft.com/v1.0"
LOGIN_URL = "https://login.microsoftonline.com"
TIME_ZONE = "Singapore Standard Time"  # Outlook's name for the venue's time zone
UTC_OFFSET = "+08:00"  # Singapore has no daylight saving
MINUTES_PER_DAY = 24 * 60

EXTENSION_NAME = "com.jiak99.booking"
EXTENSION_ID = f"Microsoft.OutlookServices.OpenTypeExtension.{EXTENSION_NAME}"
EVENT_FIELDS = (
    "id,subject,body,start,end,isAllDay,isCancelled,categories,organizer,iCalUId,createdDateTime"
)

TRIES = 3  # attempts when Microsoft is busy
LONGEST_WAIT = 30.0  # seconds; a longer Retry-After is cut to this
TOKEN_MARGIN = 300.0  # seconds: get a new token this long before the old one runs out
PAGE_SIZE = 100


class GraphError(Exception):
    """Microsoft Graph refused or failed a request. `status` is the HTTP status (0: no answer)."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class GraphSettings:
    """What the M365 admin hands over (README.md, step 7). The secret is never shown in logs."""

    tenant_id: str
    client_id: str
    client_secret: str = field(repr=False)
    mailbox: str


# --- converting between Outlook's events and the app's ---------------------------------------


def graph_time(date: str, minutes: int) -> dict[str, str]:
    """('2026-11-20', 600) -> Outlook's {'dateTime': '2026-11-20T10:00:00', 'timeZone': ...}.
    Minutes past 1440 fall on a later day."""
    days, minutes = divmod(minutes, MINUTES_PER_DAY)
    hours, mins = divmod(minutes, 60)
    return {
        "dateTime": f"{add_days(date, days)}T{hours:02d}:{mins:02d}:00",
        "timeZone": TIME_ZONE,
    }


def read_graph_time(value: dict[str, Any]) -> tuple[str, int]:
    """Outlook's {'dateTime': '2026-11-20T10:00:00.0000000', ...} -> ('2026-11-20', 600)."""
    if value.get("timeZone") != TIME_ZONE:
        raise GraphError(f"Outlook gave a time in {value.get('timeZone')!r}, not {TIME_ZONE!r}")
    text = str(value["dateTime"])
    date, clock = text[:10], text[11:16]
    return date, int(clock[:2]) * 60 + int(clock[3:5])


def event_from_graph(data: dict[str, Any]) -> CalendarEvent:
    """The app's view of one event as Graph returns it (with the booking extension expanded)."""
    date, start = read_graph_time(data["start"])
    end_date, end_minute = read_graph_time(data["end"])
    end = days_between(date, end_date) * MINUTES_PER_DAY + end_minute

    booking_id: str | None = None
    for extension in data.get("extensions") or []:
        if str(extension.get("id", "")).endswith(EXTENSION_NAME) and extension.get("bookingId"):
            booking_id = str(extension["bookingId"])

    body = (data.get("body") or {}).get("content") or ""
    organizer = ((data.get("organizer") or {}).get("emailAddress") or {}).get("address") or ""
    return CalendarEvent(
        id=str(data["id"]),
        date=date,
        start=start,
        end=end,
        subject=data.get("subject") or "",
        body=body.replace("\r\n", "\n").strip(),
        categories=tuple(data.get("categories") or ()),
        booking_id=booking_id,
        created_by=organizer,
        ical_uid=data.get("iCalUId") or "",
        created=data.get("createdDateTime") or "",
        all_day=bool(data.get("isAllDay")),
    )


def event_to_graph(event: CalendarEvent) -> dict[str, Any]:
    """The fields the app writes, as Graph wants them."""
    return {
        "subject": event.subject,
        "body": {"contentType": "text", "content": event.body},
        "start": graph_time(event.date, event.start),
        "end": graph_time(event.date, event.end),
        "isAllDay": event.all_day,
        "categories": list(event.categories),
        "showAs": "busy",
    }


def extension_json(booking_id: str) -> dict[str, str]:
    """The open extension that carries the app's booking id."""
    return {
        "@odata.type": "microsoft.graph.openTypeExtension",
        "extensionName": EXTENSION_NAME,
        "bookingId": booking_id,
    }


def _error_text(response: httpx.Response) -> str:
    """Microsoft's own explanation of a refused request, or the HTTP status."""
    try:
        data = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):  # Graph: {"error": {"code": ..., "message": ...}}
        return f"{error.get('code', '')}: {error.get('message', '')}".strip(": ")
    if isinstance(data, dict) and data.get("error_description"):  # sign-in
        return str(data["error_description"]).splitlines()[0]
    return f"HTTP {response.status_code}"


# --- the gateway -------------------------------------------------------------------------------


class GraphCalendar:
    """The main calendar of one mailbox (`BOOKINGS_MAILBOX`), through Microsoft Graph.

    `client`, `sleep` and `clock` are for tests: a client with a fake transport, and stand-ins for
    waiting and for the time (token expiry)."""

    def __init__(
        self,
        settings: GraphSettings,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._client = client if client is not None else httpx.Client(timeout=30.0)
        self._sleep = sleep
        self._clock = clock
        self._token = ""
        self._token_good_until = 0.0
        self._mailbox_path = f"/users/{quote(settings.mailbox)}"

    # --- signing in and sending ---

    def _access_token(self) -> str:
        """A valid access token, signing in again when the old one is about to run out."""
        if self._token and self._clock() < self._token_good_until:
            return self._token
        url = f"{LOGIN_URL}/{quote(self.settings.tenant_id)}/oauth2/v2.0/token"
        form = {
            "grant_type": "client_credentials",
            "client_id": self.settings.client_id,
            "client_secret": self.settings.client_secret,
            "scope": "https://graph.microsoft.com/.default",
        }
        try:
            response = self._client.post(url, data=form)
        except httpx.HTTPError as error:
            raise GraphError(f"Can't reach Microsoft sign-in: {error}") from error
        if response.status_code != 200:
            raise GraphError(
                f"Microsoft sign-in refused the app: {_error_text(response)}. Check "
                "AZURE_TENANT_ID, AZURE_CLIENT_ID and AZURE_CLIENT_SECRET (has the secret expired?).",
                response.status_code,
            )
        data = response.json()
        self._token = str(data["access_token"])
        self._token_good_until = self._clock() + float(data.get("expires_in", 3600)) - TOKEN_MARGIN
        return self._token

    def _send(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """One request to Graph. `path` starts with '/' (under v1.0) or is a full next-page link.
        Raises `GraphError` for anything but success."""
        url = path if path.startswith("https://") else GRAPH_URL + path
        signed_in_again = False
        attempt = 1
        while True:
            headers = {
                "Authorization": f"Bearer {self._access_token()}",
                "Prefer": f'outlook.timezone="{TIME_ZONE}", outlook.body-content-type="text"',
            }
            try:
                response = self._client.request(
                    method, url, params=params, json=body, headers=headers
                )
            except httpx.HTTPError as error:
                raise GraphError(f"Can't reach Microsoft Graph: {error}") from error

            if response.status_code < 400:
                return response
            if response.status_code == 401 and not signed_in_again:
                self._token = ""  # the token may have been withdrawn: sign in once more
                signed_in_again = True
                continue
            if response.status_code in (429, 503, 504) and attempt < TRIES:
                self._sleep(self._wait_seconds(response))
                attempt += 1
                continue
            raise self._error(response)

    def _wait_seconds(self, response: httpx.Response) -> float:
        """How long Microsoft asks us to wait (Retry-After), at most LONGEST_WAIT; else 2 s."""
        try:
            return min(float(response.headers.get("Retry-After", "2")), LONGEST_WAIT)
        except ValueError:
            return 2.0

    def _error(self, response: httpx.Response) -> GraphError:
        """The GraphError for a refused request, with a hint for the usual causes."""
        text = _error_text(response)
        mailbox = self.settings.mailbox
        if response.status_code == 403:
            text += (
                f". The app may not be allowed to reach {mailbox}: see README.md, 'What the "
                "Microsoft 365 admin will need to do', steps 3-4."
            )
        elif response.status_code == 404 and "MailboxNotEnabled" in text:
            text += f". Is BOOKINGS_MAILBOX ({mailbox}) right?"
        return GraphError(f"Outlook: {text}", response.status_code)

    def _event_path(self, event_id: str) -> str:
        return f"{self._mailbox_path}/events/{quote(event_id, safe='')}"

    def _read_params(self) -> dict[str, str]:
        """Which fields to read, with the booking extension."""
        return {
            "$select": EVENT_FIELDS,
            "$expand": f"extensions($filter=id eq '{EXTENSION_ID}')",
        }

    # --- the protocol ---

    def calendar_id(self) -> str:
        """The mailbox: events written to the test mailbox don't count once it changes."""
        return f"graph:{self.settings.mailbox.strip().lower()}"

    def list_events(self, first_date: str, last_date: str) -> list[CalendarEvent]:
        """Events starting on `first_date` up to and including `last_date`, sorted by date and start.
        A repeating event shows once per date. Cancelled meetings are left out."""
        params: dict[str, str] | None = {
            **self._read_params(),
            "startDateTime": f"{first_date}T00:00:00{UTC_OFFSET}",
            "endDateTime": f"{add_days(last_date, 1)}T00:00:00{UTC_OFFSET}",
            "$top": str(PAGE_SIZE),
        }
        found: list[CalendarEvent] = []
        path: str | None = f"{self._mailbox_path}/calendar/calendarView"
        while path is not None:
            data = self._send("GET", path, params=params).json()
            for item in data.get("value", []):
                if item.get("isCancelled"):
                    continue
                event = event_from_graph(item)
                # The view also holds events that started the day before and run into first_date.
                if first_date <= event.date <= last_date:
                    found.append(event)
            path = data.get("@odata.nextLink")
            params = None  # the next-page link already holds them
        found.sort(key=lambda e: (e.date, e.start, e.id))
        return found

    def get_event(self, event_id: str) -> CalendarEvent:
        """The event with this id. Raises `EventNotFound`."""
        try:
            response = self._send("GET", self._event_path(event_id), params=self._read_params())
        except GraphError as error:
            if error.status == 404:
                raise EventNotFound(event_id) from error
            raise
        return event_from_graph(response.json())

    def find_event_by_ical_uid(self, ical_uid: str) -> CalendarEvent | None:
        """The event whose iCalendar UID is `ical_uid` (as in an .ics file), or None."""
        if not ical_uid:
            return None
        quoted = ical_uid.replace("'", "''")
        params = {**self._read_params(), "$filter": f"iCalUId eq '{quoted}'"}
        data = self._send("GET", f"{self._mailbox_path}/events", params=params).json()
        items = data.get("value", [])
        return event_from_graph(items[0]) if items else None

    def create_event(self, event: CalendarEvent) -> CalendarEvent:
        """Add `event` (its `id` is ignored) and return it with the id Outlook gave it."""
        body = event_to_graph(event)
        if event.booking_id is not None:
            body["extensions"] = [extension_json(event.booking_id)]
        data = self._send("POST", f"{self._mailbox_path}/calendar/events", body=body).json()
        return replace(
            event,
            id=str(data["id"]),
            ical_uid=data.get("iCalUId") or "",
            created=data.get("createdDateTime") or "",
        )

    def update_event(self, event: CalendarEvent) -> CalendarEvent:
        """Replace the event with the same id. Raises `EventNotFound`."""
        path = self._event_path(event.id)
        try:
            self._send("PATCH", path, body=event_to_graph(event))
        except GraphError as error:
            if error.status == 404:
                raise EventNotFound(event.id) from error
            raise
        self._set_booking_id(path, event.booking_id)
        return event

    def _set_booking_id(self, event_path: str, booking_id: str | None) -> None:
        """Write (or, for None, remove) the booking extension on an existing event."""
        extension_path = f"{event_path}/extensions/{EXTENSION_NAME}"
        try:
            if booking_id is None:
                self._send("DELETE", extension_path)
            else:
                self._send("PATCH", extension_path, body=extension_json(booking_id))
        except GraphError as error:
            if error.status != 404:
                raise
            if booking_id is not None:  # no extension yet: add it
                self._send("POST", f"{event_path}/extensions", body=extension_json(booking_id))

    def delete_event(self, event_id: str) -> None:
        """Remove the event. Raises `EventNotFound`."""
        try:
            self._send("DELETE", self._event_path(event_id))
        except GraphError as error:
            if error.status == 404:
                raise EventNotFound(event_id) from error
            raise
