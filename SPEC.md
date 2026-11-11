# Zone Capacity App — Build Spec (v0.7, for Claude Code)

> **v0.7 (2026-11-11): milestones re-ordered.** Milestone 4 ends with everything that works against the fake
> calendar. A new Milestone 5 (live Outlook and go-live) takes the real Graph gateway, the M365 admin's setup,
> the completed review sheet and the real take-over. The cloud move becomes Milestone 6, Phase 2 becomes 7.
> §6 shows each milestone's status. See `DECISIONS.md` 2026-11-11.
>
> **v0.6 (2026-11-11): app first, Outlook as the fall-back.** Sales book in the app (or the Outlook add-in).
> Typing a booking into Outlook is the fall-back for when the app is down; the app takes those entries in when it is
> back (fail-back). Staff keep edit rights on the calendar. Adds an Outlook sync warning and database backups.
> The app runs on the owner's laptop first (one user, no sign-in yet, SQLite) and moves to the cloud later (§5.5), with
> hourly backups and a restore path for a lost or broken laptop (§5.6). New Milestone 4L (laptop go-live); the
> cloud move becomes Milestone 5 and takes in the Outlook add-in (Milestone 6 since v0.7).
> See `DECISIONS.md` 2026-11-11, §5.4 "When the app is down", §5.5 and §5.6.
>
> **v0.5 (2026-11-10): Outlook calendars.** Bookings live in the main calendar of the existing `operations@` mailbox.
> That calendar already holds today's bookings in free text and is the ground truth: the app
> takes it over once (past entries into a separate history table, future ones checked and rewritten in place).
> Lena's personal calendar is not used. Event bodies get a fixed layout.
> See `DECISIONS.md` 2026-11-10 and §5.4.
>
> **v0.4 (2026-11-09): zone routes.** Zones are no longer identical. Each zone has its own activity, and a group
> moves through several zones during its stay (its *route*). Ticket lengths changed, Tingkat Meals was added, and
> large groups are now split evenly. See `DECISIONS.md` 2026-11-09.

Source of truth for building the app. Product rationale lives in the product spec doc; this file is the
implementation contract. Where this file and the doc disagree, this file wins.

---

## 1. What we are building

An engine plus thin UI that helps an F&B attraction sell its 4 zones and plan each group's route through them.

- **Sales manager**: an enquiry arrives (date, start time, group size, ticket type). The app returns a
  verdict on the request *as asked* and up to 3 alternative pitches that use space better. One click books it.
- **Operations manager**: sees groups on a zone timeline, has the app plan each group's zone order (route),
  pins routes, and locks the day's plan.
- **Outlook (Microsoft 365) is the user layer.** Bookings and plans are mirrored into Outlook calendars via
  Microsoft Graph. The engine's database is the system of record.

There is **no hold state**. An enquiry is either booked or nothing is stored.

---

## 2. Business rules (fixed)

| Rule | Value |
| --- | --- |
| Zones | 4, each with its own activity: `A` Tour/Exhibit, `B` Dining, `C` Chef's Table / Dining, `D` Sandart |
| Capacity per zone | 25 persons; adults and children each count as 1 |
| Opening hours | 09:00–21:00, every day |
| Time slots | The day is cut into 30-min slots (24 per zone). Every visit to a zone starts and ends on a slot boundary |
| Start times | Every 30 min, 09:00–20:30 (24 start times) |
| Ticket types | See the zone-time table below. Codes are stored; names are display only |
| Route | A group visits each zone of **one of its ticket's allowed zone sets** (below) once, for the time shown, **back-to-back with no gaps**, starting at its start time. The app picks **both the zone set and the order** — any that fits. A 60-min visit is one continuous hour in that zone, never split |
| Visit must end by | 21:00 → latest start: TINGKAT 20:30, CLASSIC 20:00, CLASSIC_CT 19:30, PREMIUM 19:00, PREMIUM_CT 18:00 |
| No sharing | **A 30-min slot of a zone holds one group at most, whatever its size.** Once a group is in a zone for a slot, that zone is sold out for that slot. Spare seats are never sold to another booking |
| Groups over 25 | Split into `ceil(N / 25)` sub-groups **as equal in size as possible** (26 → 13 + 13, 40 → 20 + 20, 60 → 20 + 20 + 20). Each sub-group has the same start time and ticket but its own route. A group of 25 or fewer is never split |
| Concurrency | Any number of groups may be in the building at once, as long as no zone slot holds two groups. Nothing else (e.g. the Chef's Table) limits it |
| Minimum group size | None |
| Booking window | Today up to 6 months ahead (183 days) |
| Time zone | Asia/Singapore (UTC+8, no DST) |

**Allowed zone sets and time in each zone, per ticket type**

| Ticket | Allowed zone sets (pick one) | Time per zone | Total stay | Possible routes |
| --- | --- | --- | --- | --- |
| `TINGKAT` | {B} or {C} or {D} | 30 min | 30 min | 3 |
| `CLASSIC` | {A, D} or {A, B} or {A, C} | 30 min each | 1 h | 6 |
| `CLASSIC_CT` | {A, C, D} or {A, B, D} | 30 min each | 1.5 h | 12 |
| `PREMIUM` | {A, B, C, D} | 30 min each | 2 h | 24 |
| `PREMIUM_CT` | {A, B, C, D} | A 30, B 60, C 60, D 30 min | 3 h | 24 |

In short: every ticket except Tingkat visits A once. Classic adds one of B, C or D. Classic + CT adds D and one of
B or C. Premium and Premium + CT visit all four zones.

| Code | Name (`TICKET_NAMES`) | Short name (`TICKET_SHORT_NAMES`) |
| --- | --- | --- |
| `TINGKAT` | Tingkat Meals | Tingkat |
| `CLASSIC` | Classic Hawker Experience | CHE |
| `CLASSIC_CT` | Classic Hawker Experience + Chef's Table | CHE+CT |
| `PREMIUM` | Premium Hawker Experience | PHE |
| `PREMIUM_CT` | Premium Hawker Experience + Chef's Table | PHE+CT |

A route is written as the zone letters in visiting order, e.g. `ABDC`. For `PREMIUM_CT`, `ABDC` means
A 30 min, B 60 min, D 30 min, C 60 min (11:00 start → 11:00–14:00). A `CLASSIC` route `BA` means B 30 min then
A 30 min. For `TINGKAT` the route is one letter (`B`, `C` or `D`). The letters in a route show which zone set was
used.

### Configurable settings (with defaults — the business has not decided these yet)

| Setting | Default | Notes |
| --- | --- | --- |
| `turnaroundMinutes` | `0` | Zone reset time after each group's visit to a zone, before the next group may enter it. Must support values like 15. Since visits start on slot boundaries, any value above 0 blocks the zone up to the next slot boundary (15 → the next 30-min slot). Not counted as wasted time. Never applies between one group's own visits |
| `smallGroupWarnBelow` | `0` (off — business decision 2026-11-07: leave off for now) | If > 0, a *new* session with fewer people is flagged COSTLY (advisory only, never blocks) |
| `alternativeDaySearch` | `7` | Days either side searched for "another day" alternatives |
| `availabilityCalendarDays` | `60` | How far ahead the Outlook Availability calendar is written |
| `prices` | `null` | Optional price per adult/child per ticket type; used only as a ranking tie-break and in metrics |
| Per-date overrides | none | Blackout times / closed days, per date |

Do not hard-code any of these; read them from a settings table with these defaults.

---

## 3. Domain model

Represent time as **minutes since midnight** (`int`), dates as `YYYY-MM-DD` local (Singapore, `str`). No
`datetime` / `date` objects inside the engine.

The notation below is language-neutral. In Python, implement entities as frozen `dataclass`es with type hints,
enumerations as `Literal` types or `StrEnum`, `?` as `X | None`, and `camelCase` names as `snake_case`
(e.g. `zoneId` → `zone_id`). JSON field names in the API may stay `camelCase` via Pydantic aliases.

```
Zone        { id: 'A'|'B'|'C'|'D', name, capacity: 25, active: boolean }
TicketType  { id: 'TINGKAT'|'CLASSIC'|'CLASSIC_CT'|'PREMIUM'|'PREMIUM_CT', name, shortName,
              minutes: 30|60|90|120|180, zoneSets (allowed zone sets and time per zone, §2), priceAdult?, priceChild? }
Session     { id, date, start (min), type, route: string|null (e.g. 'ABDC'), pinned: boolean,
              status: 'open'|'locked'|'in_progress'|'done' }
Visit       (derived, not stored) { sessionId, zoneId, start, end } — from start + type + route
Booking     { id, sessionId, customerId, adults, children, status: 'confirmed'|'cancelled'|'no_show',
              notes, createdBy, outlookEventId?, createdAt, updatedAt }
Customer    { id, name, phone?, email?, company? }
DaySettings { date, lockedAt?: timestamp, overrides (blackouts) }
Settings    { turnaroundMinutes, smallGroupWarnBelow, alternativeDaySearch, availabilityCalendarDays, prices }
AuditLog    { id, actor, action, entity, entityId, before, after, at }
PastBooking { id, date, start (min), end (min), people?, type?, typeSource: 'subject'|'client'|'review'|null,
              client, status: 'confirmed'|'cancelled', wasSoftBlock: boolean, sourceEventId (unique),
              originalSubject }
```

- A **PastBooking** is history from before the app (§5.4 "One-off migration"). It is read-only: the engine never
  plans it, it is never written to Outlook, and no business rule is checked against it. It holds no body text,
  phone numbers or email addresses. `type` is null when it can't be told; `typeSource` says how it was found.

- A session is **one group moving through its route**. It belongs to exactly **one booking**. A booking over 25
  has one session per sub-group (§2 "Groups over 25"). `Session.headcount` = the people in that sub-group.
- A session with zero confirmed bookings is deleted.
- Each visit occupies its zone for `[visitStart, visitEnd + turnaroundMinutes)`, the end rounded up to the next
  30-min slot boundary. Turnaround after 21:00 is irrelevant (clip to close).
- `route` is `null` ("floating") until allocation. Sessions are **pinned** (ops fixed the route) or **locked**
  (day plan locked) or **in progress** → their `route` must never change automatically.
- A route is valid for its type when its letters are exactly one of that type's allowed zone sets (§2), each once.

---

## 4. The engine (pure Python package, no I/O)

All functions are pure: `(dayState, settings, input) → result`. This is the core of the product and must be
heavily unit-tested. Standard library only (no third-party runtime dependencies).

### 4.1 Feasibility and route assignment

`assign(sessions, settings) → { ok: true, assignment: dict[sessionId, route] } | { ok: false }`

- Fixed sessions (pinned / locked / in progress / done) keep their route.
- Each floating session gets a valid route for its type (§3) such that no two visits overlap in any zone
  (including turnaround). Possible routes per session (zone set × order): TINGKAT 3, CLASSIC 6, CLASSIC_CT 12,
  PREMIUM 24, PREMIUM_CT 24.
- Must find an assignment **if one exists**. The day is small (4 zones × 24 slots = 96 zone slots), so an exact
  search is fine: depth-first search with backtracking over sessions sorted by start, the occupied zone slots
  kept as one bitmask per zone, with memoisation on (next session, occupied slots).
- Among valid assignments, prefer the one with **minimum dead minutes** (4.2). Stop the optimisation when the
  **search budget** runs out and fall back to the best assignment found.
- **Search budget**: one budget covers a whole `checkEnquiry` call, including every alternative candidate
  (4.4), not each `assign` call separately. Default 200 ms in total, shared by all the `assign` calls it makes;
  once it is spent, the remaining calls return their first valid assignment. The budget only cuts the dead-minute optimisation short; feasibility must still be decided exactly.
  To keep the engine pure and tests deterministic, pass the budget in as a parameter with an injectable clock
  (default `time.monotonic`). This is the only clock the engine may read.

### 4.2 Dead minutes (unsellable zone slots)

Given an assignment, a zone slot (zone × 30-min slot) is **free** if no visit (incl. its turnaround, rounded up
to the slot boundary) and no blackout covers it. Slots blocked only by turnaround are structural, never dead.

A free zone slot is **sellable** if one more group of any ticket type **except `TINGKAT`** could use it: there is
a ticket type, a valid start time and a route whose visits include that zone slot and use only free zone slots
(with turnaround around each visit), ending by 21:00. Tingkat is ignored here because it fits almost any free
slot; counting it would hide real waste.

Otherwise the zone slot is **dead**. `deadMinutes(day)` = 30 × number of dead zone slots over all 4 zones under
the best assignment.

Note: every ticket except Tingkat visits zone A, so a free B, C or D slot is dead when no free A slot is close
enough to it (within one route) — even if a Tingkat could still use it.

### 4.3 Enquiry check

```
checkEnquiry(day, settings, { date, start, type, adults, children }) → {
  verdict: 'ACCEPT_GOOD' | 'ACCEPT_COSTLY' | 'NOT_POSSIBLE',
  placement: { kind: 'new', people, route } | { kind: 'split', newSessions: [{ people, route }, …] } | null,
  deadMinutesDelta: number,      // after − before
  zoneMinutesAdded: number,      // sub-groups × ticket minutes
  reason: string,                // one line, plain English, shown to sales
  alternatives: Alternative[]    // ≤ 3, see 4.4
}
```

Rules, in order:

1. **Validity**: start on the grid, `start + minutes ≤ 21:00`, date within the booking window and not a
   closed/blackout time → otherwise `NOT_POSSIBLE` with the reason (e.g. "A 2 h PHE from 19:30 would end
   after the 21:00 close").
2. **Own zones**: the group never joins another group in a zone slot (§2 "No sharing").
3. **New session**: add a floating session and run `assign`. If infeasible → `NOT_POSSIBLE`.
   If feasible: `ACCEPT_COSTLY` when `deadMinutesDelta > 0`, or when `smallGroupWarnBelow > 0 && N <
   smallGroupWarnBelow`; otherwise `ACCEPT_GOOD`.
4. **Groups over 25** (`N > 25`): open `ceil(N / 25)` new sessions, sizes as equal as possible (§2);
   feasibility and costliness as in step 3. A booking of ≤ 25 is never split.

`reason` examples (must be generated, not hard-coded):
- "Fits (route ADBC) with no unsellable slots."
- "Group of 40 at 09:00 · PHE is split into 2 groups (20 + 20). Fits (routes ABCD, BADC) with no unsellable slots."
- "Fits, but leaves B 20:00–20:30 and C 20:30–21:00 unsellable."
- "Zone A is in use 10:00–11:00." / "Zones B, C and D are in use at 10:00."

### 4.4 Alternatives

Generate candidates, evaluate each with `checkEnquiry` (without recursion into alternatives), keep only those
**strictly better** than the request's own result, rank, return the top 3.

Candidates:
1. **Shift time**: same type, start ±30 and ±60 min.
2. **Change type**: same start, other ticket types — **upgrades only, never a downgrade**. A downgrade is a lower
   level (Premium → Classic) or losing the Chef's Table; so Classic + CT ↔ Premium is a downgrade either way.
   Allowed: Tingkat → any other type; Classic → Classic + CT / Premium / Premium + CT; Classic + CT → Premium + CT;
   Premium → Premium + CT. **Tingkat is never offered as a change of type.** (A Tingkat request may still get
   Tingkat at another time or day.)
3. **Another day**: same start and type on dates +1, −1, +2, −2 … up to `alternativeDaySearch`, inside the booking
   window, not in the past. Only the nearest qualifying day is kept.

"Better" ordering (lexicographic):
1. Verdict: `ACCEPT_GOOD` > `ACCEPT_COSTLY` > `NOT_POSSIBLE`
2. `deadMinutesDelta` (lower is better)
3. `zoneMinutesAdded` (lower is better) — implemented as *number of new sessions (sub-groups)*, see `DECISIONS.md`
   (needed for T5)
4. Change size (lower is better): time shift = 1 per 30 min; type change = 1; day change = 2 + |days|.
   Ties: time shift before type change before day change.
5. Revenue (higher is better) if `prices` set.

Each alternative carries `{ date, start, type, verdict, changeSummary, gainSummary }`, e.g.
`changeSummary: "Start 11:00 instead of 10:30"`, `gainSummary: "Fits with no unsellable slots"`.

### 4.5 Availability board

`availability(day, settings, N) → for each start time × type: 'available' | 'full' | 'not_offered'`
(`available` = a route can be found for every sub-group of the group, with `assign` still succeeding).

### 4.6 Day metrics

| Metric | Formula |
| --- | --- |
| Seat-hour use | Σ(headcount × session hours) ÷ (100 × 12) (a group is in one zone at a time) |
| Zone-hour use | Σ visit hours ÷ (4 × 12) (equals Σ session hours) |
| Average session fill | mean(headcount ÷ 25) |
| Dead gap hours | deadMinutes ÷ 60 |
| Revenue per zone-hour | revenue ÷ Σ visit hours (only if prices set) |

---

## 5. Application

### 5.1 Stack (default — change only with a reason recorded in `DECISIONS.md`)

- Python 3.13, **uv** workspace (one `uv.lock` at the repo root; each package has its own `pyproject.toml`).
- Tooling: `pytest`, `hypothesis`, `pytest-cov`, `ruff` (lint + format), `mypy --strict`.
- `packages/engine`: pure logic, standard library only, 100% of section 4 covered.
- `apps/api`: FastAPI + Pydantic v2, SQLAlchemy 2.0 + Alembic migrations, PostgreSQL (SQLite allowed for local
  dev and for the laptop phase, §5.5; the concurrency tests in §8 must run against PostgreSQL). Per-date booking lock: PostgreSQL
  `pg_advisory_xact_lock` inside the booking transaction. JSON API mounted under `/api`.
- **Web UI**: server-rendered by `apps/api` with Jinja2 templates + HTMX (small vanilla JS only where needed,
  e.g. the Ops timeline). No SPA framework or JS build step. Pages: Enquiry check, Availability board,
  6-month calendar, Ops timeline, Settings. The Enquiry check page must also work inside an Outlook add-in task
  pane (narrow, ~350 px wide).
- `packages/graph`: Microsoft Graph wrapper (`msal`, `msgraph-sdk`) behind a `GraphGateway` `typing.Protocol`, with
  an in-memory fake for tests and local dev.
- Auth: Microsoft Entra ID sign-in (MSAL); app roles `Sales` and `Operations` (Operations can do everything
  Sales can).
- `apps/outlook-addin`: the only non-Python part. A manifest plus a minimal HTML/TypeScript task-pane shell that
  loads the Enquiry check page from `apps/api`; Office.js used only to prefill from the open item. Node is
  needed only for this, at Milestone 6.
- Docker Compose for local dev (api + db).

### 5.2 Repo layout

```
/packages/engine      domain types, assign, dead_minutes, check_enquiry, alternatives, availability, metrics
/packages/graph       GraphGateway protocol + real + fake implementations, subject-template parser,
                      reader for old-style subjects (one-off migration only)
/apps/api             JSON routes, HTML pages (templates/, static/), persistence, migrations, sync jobs, auth,
                      import-outlook command (one-off migration)
/apps/outlook-addin   manifest + task pane shell (Milestone 6)
pyproject.toml  uv.lock  docker-compose.yml  .env.example
SPEC.md  CLAUDE.md  DECISIONS.md  README.md
```

Python packages use a `src/` layout (e.g. `packages/engine/src/engine/`, tests in `packages/engine/tests/`).

### 5.3 API (minimum)

Paths below are relative to `/api`. Path parameters are written `:id` here; in FastAPI they are `{id}`.

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| GET | `/enquiries/check?date&start&type&adults&children` | Sales | → `checkEnquiry` result. Stores nothing; response sent with `Cache-Control: no-store` |
| POST | `/bookings` | Sales | Book the request or an alternative. Re-runs the check **inside a transaction** with a per-date lock; returns 409 with a fresh check if it no longer fits |
| PATCH | `/bookings/:id` | Sales | Edit (re-checks) |
| POST | `/bookings/:id/cancel` | Sales | Cancel |
| POST | `/bookings/:id/no-show` | Operations | Mark no-show |
| GET | `/days/:date` | Both | Sessions, bookings, assignment, metrics |
| GET | `/availability?date&n` | Both | Availability board |
| GET | `/calendar?from&to` | Both | Per-day seat-hour use for the 6-month view |
| POST | `/days/:date/allocate` | Operations | Run `assign`, persist routes for floating sessions |
| POST | `/days/:date/lock` / `unlock` | Operations | Lock freezes every session's route |
| PATCH | `/sessions/:id/pin` | Operations | Pin/unpin a route |
| GET/PUT | `/settings` | Operations | Settings in section 2 |

All writes go to `AuditLog`.

### 5.4 Outlook / Microsoft Graph sync

Tenant: Microsoft 365 for business (confirmed). Use an Entra app registration with **application** permission
`Calendars.ReadWrite`, admin-consented, and restrict it to the mailboxes below using Exchange Online application
access policy / RBAC for Applications. Never request tenant-wide mail access. The app touches calendars only,
never mail.

Mailboxes / calendars (addresses from env vars):

| Calendar | Mailbox | Written by | Content |
| --- | --- | --- | --- |
| Bookings | **main calendar** of the existing `operations@` mailbox (`BOOKINGS_MAILBOX`) | App; Sales by hand only as the fall-back (see "When the app is down") | One event per booking, Busy, covering the whole stay. Subject `PHE+CT · Tan family · 18 pax · ABDC · 11:00–14:00` (short name · customer · people · route · times). A split group lists every sub-group's route: `PHE · Lee wedding · 40 pax · ABCD/DCBA · 10:00–12:00`. Body in the fixed layout below. Category = ticket type colour. Booking id stored in an open extension |
| Availability (**after Milestone 5**) | same mailbox, calendar "Availability" | App only | One event per start time with something sellable, next `availabilityCalendarDays` days, e.g. "10:30 · CHE, CHE+CT, PHE" |

Staff see the Bookings calendar in their own Outlook as a shared calendar (the admin grants calendar permission
on `operations@`; listed in `README.md`). Staff keep **Editor** rights: the calendar is never made read-only for
them, so bookings can still be taken when the app is down. "Book in the app" is a working rule, not a permission.

**How sales book**: in the app's Check and book page, or (Milestone 6) the same page in the Outlook add-in.
Every booking then passes the capacity check before it exists. Typing a booking into Outlook is only for when
the app is down.

**Event body** (written by the app; the same layout for every booking, so staff always find things in one place):

```
Booking: <id>   Ticket: Premium + Chef's Table   Status: confirmed
Customer: Tan family   Contact: <name>, <phone / email>
People: 18 (15 adults, 3 children)   Route: ABDC   Times: 11:00–14:00
Notes: <free text: menu or set-meal choice, workshop item, dietary needs, tour operator>
```

Contact and notes come from the booking (`Customer`, `Booking.notes`); empty lines are left out.

**Text kept from before the app.** When the app takes over an event it didn't create (the one-off take-over
below, or a hand-typed entry it books), it keeps the event's old body: the app's layout goes on top, then a
divider line `── Before the app ──`, then the old text exactly as it was (forwarded emails, contacts, menu,
dietary needs). The app only ever writes the part above the divider; everything below it is never changed,
and a sync that compares the event with the booking ignores it. The kept text stays in Outlook only: it is
not copied into the app's database. Events the app creates itself have no divider.

There are **no per-zone calendars** (no room mailboxes): zone assignments are not shown in Outlook beyond the
route in the booking subject.

The route in the subject is the **current** plan. Until the day is locked it may change as other bookings
arrive; the app then updates the subject (same as any other booking change).

Sync rules:
- **App → Outlook**: on every booking/session change, upsert the affected events (idempotent by stored ids).
  Availability calendar: debounce, rewrite affected dates within 60 s.
- **Outlook → App** (entries typed by hand in the Bookings calendar, mainly during an outage, and changes to
  the app's own events made in Outlook): Graph change notifications on the Bookings calendar, plus a delta-query
  sweep every 5 min as a fallback. Renew subscriptions before expiry.
  - Event without our extension → parse the subject with the template parser:
    `^\[(tingkat|classic|classic[ _+-]?ct|premium|premium[ _+-]?ct)\]\s*(\d{1,2}:\d{2})\s*[·|\-]\s*(\d+)A(?:\s*(\d+)C)?\s*[·|\-]\s*(.+)$`
    (case-insensitive, tolerant of spacing; `[Classic CT]`, `[classic-ct]`, `[CLASSIC_CT]` all mean `CLASSIC_CT`).
    Date = event date. This is the format sales use when the app is down; the app then rewrites the subject
    into the format above.
  - Parsed and fits → create booking, attach extension, normalise the event's times/subject, and write the
    app's layout above what sales typed (kept below the divider, see "Text kept from before the app").
  - Parsed but does not fit → do not create; set category "Conflict" and put the check result and
    alternatives at the top of the event body (above what sales typed, which is kept).
  - Unparseable but booking-like (same test as the take-over below: a status tag, headcount or ticket word in
    the subject) → category "Needs format" and put the expected format at the top of the event body.
  - Anything else (meetings, site visits, staff events) is left alone.
  - Several new entries at once (e.g. after an outage) → taken in oldest first (by when the event was created),
    so whoever booked first keeps the slot.
  - No email is sent: sales see these categories and notes in the calendar itself (the app has calendar
    access only).
  - Event with our extension edited or deleted in Outlook → treat as edit / cancel. An edit is re-checked; if
    it doesn't fit, the booking stays as it was, the event is put back to match it, and the reason goes at the
    top of the event body with category "Conflict".
- There is no hold state (§2). The one-off migration below turns existing soft blocks into confirmed bookings.

**When the app is down (fall-back and fail-back).** Outlook is Microsoft's service and keeps working when the
app doesn't, and every future booking is in it with its full details (subject and body above).

- **Fall-back** (app down): sales see all bookings in Outlook as usual and type new ones into the Bookings
  calendar in the template format above. There is no capacity check meanwhile, so they judge from the calendar
  (rule of thumb for staff, in `README.md`: if four groups already overlap that time, don't book it) and tell
  the customer the details follow. Changes and cancellations are made on the event itself.
- **Fail-back** (app back up), in this order:
  1. App → Outlook: write every booking change still waiting (the sync already catches up after an outage).
  2. Outlook → App: read every event from today to +6 months in full, not only the changes since the last
     sweep. This is also done every time the app starts, and whenever the last successful sweep is more than
     30 min old (e.g. the laptop was asleep, §5.5).
  3. Take in hand-typed entries oldest first through the normal check (rules above). Clashes get "Conflict"
     for ops to sort out with the customer.
  4. Edits and deletions of the app's own events made meanwhile become edits and cancels (rules above).
- **Outage in the other direction** (app up, Outlook unreachable or its sign-in expired): sales keep booking
  in the app; Outlook catches up when it is back (step 1).

**Outlook sync warning.** The app keeps when it last wrote to and read from Outlook successfully, and the last
error. Every page shows a banner ("Outlook not updated since 14:05: <reason>") when the last attempt failed,
when changes have been waiting for more than 10 min, or when the sweep hasn't succeeded for 30 min. If
`AZURE_CLIENT_SECRET_EXPIRES` (`YYYY-MM-DD`, optional) is set, the banner also warns from 30 days before the app's
Outlook sign-in expires. `GET /api/health` (no sign-in) returns the same status plus whether the database
answers, so an uptime checker can watch it.

**One-off migration: taking over the Bookings calendar** (`uv run import-outlook`, dry run by default). The
main calendar of `operations@` already holds the bookings, typed by hand in free text since 2026-10-08. It is
the ground truth. Lena's personal calendar is not used.

1. Read the calendar's events. Source: an `.ics` file saved from it (`--from-ics FILE`) or, once Graph is set up,
   the calendar itself (`--from-graph`). Only booking-like events are kept: a headcount ("18 pax", "34 students +
   4 teachers = 38 pax", "40 kids"), a ticket word (CHE, PHE, + CT, Chef Table, Tingkat, Classic, Premium) or a
   status tag (`[Confirmed]`, `[Soft Block]`). Meetings and other items are left alone and not stored.
2. Read each old-style entry with a forgiving reader: status tag, `Canceled:` prefix, headcount (from the subject,
   else from the body), ticket words, client name. Known clients map to a ticket type (`Sunshine … TourGroup` →
   `TINGKAT`); the map is a small settings file, not code. **Soft blocks count as confirmed.**
3. Write a **review CSV** (one row per event: date, start, end, people, type, how the type was found, client,
   status, was soft block, past/future, proposed action, original subject). Nothing is stored. The owner corrects
   it (e.g. fills in missing headcounts).
4. `--review FILE --commit` applies the corrected CSV:
   - **past** events (before the migration date) → `PastBooking` rows (history);
   - **future** events, 1–100 people → real bookings through the normal enquiry check (groups over 25 split
     evenly). The existing Outlook event is **taken over in place**: subject rewritten in the new format,
     the app's layout put above the old body (kept below the divider), booking id attached. No second event is created. Any that don't fit are listed for sales to sort
     out by hand (not forced in, event left unchanged);
   - **future events over 100 people** → not booked (too big for one start time until Phase 2). Listed, so ops
     can block the whole venue for that time with a blackout on the Day plan page;
   - cancelled events → history only if past, otherwise skipped;
   - re-running is safe: an event already migrated (same Outlook event id) is skipped.

**Test data**: the repo is public, so no real calendar data is ever committed (names, phone numbers, emails,
meeting links or passcodes). Tests use anonymised events made by a committed script from the real calendar file
(clients replaced by consistent made-up names, bodies dropped, output reviewed before commit), plus local-only
tests that run against the real file when `JIAK_REAL_CALENDAR_ICS` points at it and are skipped otherwise.

### 5.5 Hosting: laptop first, cloud later

**Laptop phase (now).** The app runs on the business owner's laptop. The owner is its only user and the only one
taking bookings.

- The app listens on `127.0.0.1` only (this laptop), because it has no sign-in yet. Never on the network.
- No sign-in: the audit log records the owner's name from a setting (`JIAK_USER`) instead of the dev default.
- Database: **SQLite** (owner, 2026-11-11; reasons in `docs/DATABASE-CHOICE.md`), made safe for real bookings:
  1. the live file is in a local app folder (`%LOCALAPPDATA%\Jiak99\`), never in a folder OneDrive syncs
     (Documents and Desktop often are);
  2. WAL mode and a longer busy wait, so the page, the Outlook sync and the sweep can write at the same time;
  3. every booking transaction takes SQLite's write lock at its start (`BEGIN IMMEDIATE`), so a second program
     (e.g. `uv run import-outlook`) can't double-book while the app runs;
  4. backups with SQLite's backup function (§5.6);
  5. forms and API enforce the database's text limits, so nothing is stored that PostgreSQL would refuse at the
     cloud move.
- Tests run on SQLite and PostgreSQL for every push (GitHub Actions), so the cloud database stays supported.
- The app starts by itself when the owner signs in to Windows. Updating to a new version is one script, which
  also upgrades the database (Alembic).
- Outlook connection: the application permission of §5.4, as for a server, so the cloud move only copies the
  settings. Its secret is in `.env` on the laptop.
- No Graph change notifications (they need a public HTTPS address): the 5-min sweep and the catch-up rule in
  §5.4 "When the app is down" (step 2) do the job. A laptop that is off or asleep counts as "app down".
- The laptop has BitLocker on. Backups and recovery: §5.6.
- Deferred to the cloud move: Microsoft sign-in and roles, the Outlook add-in, Graph change notifications.

**Cloud move (later).** Move to a cloud server (with HTTPS, sign-in and managed PostgreSQL) when any of these
happens: a second person needs to book or plan in the app; the laptop being off causes clashes or late bookings;
the owner is away and nobody can run the app; the Outlook add-in is wanted. Data size and speed are not
reasons: the laptop copes with them.

### 5.6 Laptop lost or broken (laptop phase only)

Outlook keeps every future booking while the laptop is gone (§5.4 fall-back). What can be lost is what only the
app holds: the latest changes, history, audit log and settings.

**Backups.** While the app runs, it makes a consistent copy of the database every hour and when it closes
(SQLite's backup function, safe while the app writes), into a folder synced to the owner's OneDrive for Business
(not a personal cloud: it is customer data). It keeps hourly copies for 2 days and one a day for 30 days. A backup that
fails shows in the Outlook sync warning banner (§5.4). A restore is tried once before go-live.

**Restore.** `uv run restore FILE` checks the backup, puts it in place (the database it replaces is set aside,
never deleted), upgrades it to the latest version, and starts the app with **Outlook sync paused**. Outlook is
newer than the backup, so the normal sync would do the wrong thing: bring back events of bookings cancelled after
the backup, undo later changes, and not know bookings made after it. While paused, the app reads Outlook but
writes nothing to it and takes nothing in from it; every page shows a "Restored from backup: Outlook sync paused"
banner. Bookings can still be checked and made.

**After-restore list** (a page; list only, nothing is changed automatically). It compares Outlook (today to
+6 months) with the restored database and lists:
1. Outlook events with a booking id the database doesn't have (booked after the backup) → the owner books them
   in the app, then deletes the old event in Outlook;
2. bookings whose Outlook event is gone (most likely cancelled after the backup) → the owner cancels them in the
   app, or leaves them if they are still on;
3. bookings whose Outlook event differs in date, time, ticket, people or customer (most likely changed after the
   backup) → the owner edits the booking in the app to match, or leaves it;
4. hand-typed entries, for information: they are taken in by the normal fail-back after resuming.

The list updates as the owner works through it. **Resume Outlook sync** (a button on the page) makes the
database the truth again: the normal sync writes every booking to Outlook (re-creating missing events, rewriting
those that differ), then the fail-back of §5.4 runs. Events still carrying an unknown booking id are never
deleted by the app; they get category "Not in app" and a note at the top of the body ("Not in the app: booked
after the last backup. Book it in the app, then delete this event.").

**Targets.** At most about an hour of app-only changes lost (and those bookings are still visible in Outlook).
Running again on a replacement laptop within one working day.

**Lost or stolen.** The steps for the owner and the M365 admin are a checklist in `README.md`: the admin revokes
the app's Outlook secret at once and issues a new one for the replacement laptop; BitLocker keeps the data on
the lost laptop unreadable (its recovery key kept in the business's Microsoft 365 account); the owner checks
whether the PDPA data breach rules require a report. A replacement laptop is set up with one script, then
`uv run restore`.

After the cloud move, the cloud database's own backups replace this section.

---

## 6. Milestones (build in this order; each ends green)

Status on 2026-11-11: Milestones 1–4L are done; Milestone 5 is next.

1. **Engine** — section 4 complete with the acceptance tests in section 7. No I/O.
   *Done (2026-11-08, in `main`).*
2. **API + DB** — section 5.3 with SQLAlchemy models + Alembic migrations, seed script (sample days),
   transaction-safe booking. *Done (2026-11-08, in `main`).*
3. **Web UI** (Jinja2 + HTMX pages served by `apps/api`) — Enquiry check (verdict, reason, 3 alternatives, Book
   buttons), Availability board, 6-month calendar (colour by seat-hour use), Ops timeline (zones × time, dead gaps
   shaded, pin, allocate, lock), Settings. Reshaped as Check and book → Bookings → Day plan (`DECISIONS.md`
   2026-11-09). *Done (2026-11-09, in `main`).*
3R. **Zone routes (v0.4 rules)** — rework Milestones 1–3 for §2 v0.4: engine (routes, Tingkat, even splits,
   dead zone slots, new §7 tests), database migration (`zone` → `route`), example days and scenarios, CLI boards,
   API and pages (timeline shows each group's visits across zones). Uses example days only; no real data yet.
   *Done (2026-11-10, in `main`).*
4. **Outlook sync, against the fake calendar** — `packages/graph` (GraphGateway protocol, in-memory fake, event
   format, subject readers). App → Outlook sync on every booking change. One-off take-over of the `operations@`
   calendar from an `.ics` file: dry run with the review sheet, then `--review FILE [--commit]` with the
   `PastBooking` history table and events taken over in place. Outlook → App: the sweep with the fail-back (§5.4
   "When the app is down"), old text kept below the divider, the Outlook sync warning and `/api/health`; staff
   fall-back instructions in `README.md`. *Done (2026-11-11, in `main`).*
   The real Microsoft Graph gateway moved to Milestone 5 (it needs the M365 admin's setup).
4L. **Laptop go-live** (§5.5, §5.6) — the five SQLite safeguards, tests on SQLite and PostgreSQL in GitHub
   Actions, `JIAK_USER`, hourly backups and `uv run backup`, `uv run restore` with the paused sync and the
   after-restore list, Windows scripts (setup incl. `-RestoreNewest`, start, stop, auto-start, update), the
   printable laptop guide with the "lost, stolen or broken" checklist (`docs/LAPTOP-INSTALL.md`).
   *Done (2026-11-11, in `main`).* The owner sets the laptop up and tries one restore (guide part E).
5. **Live Outlook and go-live** — everything that needs the real calendar, the M365 admin or the owner's data:
   - the **M365 admin's setup**, from a forwardable checklist (`docs/M365-ADMIN.md`): the app registration
     (`Calendars.ReadWrite`, application), access limited to the Jiak99 mailboxes, a test mailbox, a client secret
     and its expiry date, the Outlook categories, calendar access for staff;
   - the **real Graph gateway** behind `GRAPH_MODE=real` (incl. `import-outlook --from-graph`), tested against the
     test mailbox before `operations@`;
   - the **review sheet completed** by the owner (the rows marked "fill in"), from a fresh export of the calendar
     close to go-live, with corrections already made on an earlier sheet carried over (matched by event id);
   - the **real take-over** (rehearse, then `--commit`) and **go-live** (guide part F).
   The Availability calendar (§5.4) comes after this milestone.
6. **Cloud move** (when a trigger in §5.5 is hit) — Microsoft Entra sign-in with the Sales and Operations roles,
   hosting with HTTPS, managed PostgreSQL with its backups, copying the laptop's SQLite data into it (checked
   against PostgreSQL's rules first) with a rehearsal, Graph change notifications. Then the **Outlook add-in**:
   task pane hosting the Enquiry check page; "Book" creates the booking via the API (which writes the Outlook
   event).
7. **Phase 2 (only when asked)** — consolidation suggestions, day-of changes (walk-ins, late arrivals), run sheet,
   metrics dashboard. **To do:** suggest splitting a large group across start times (e.g. 60 · CHE → 30 at 10:00 +
   30 at 11:00), and bookings with more than one start time. Until then sales book each part as its own booking.
   **To do:** groups over 100 (several start times; until then ops blocks the venue with a blackout). Analytics on
   `PastBooking` history. **To do (if asked):** a manager override to book despite a refusal (e.g. to settle an
   outage clash the customer was already promised).

---

## 7. Engine acceptance tests (must pass)

Defaults: turnaround 0, `smallGroupWarnBelow` 0. "Empty" = no sessions. Times local. Routes are written as in §2
(`ABCD` = A first). "Full 09:00–11:00" = four PREMIUM sessions at 09:00, which fill every zone slot from 09:00 to
11:00 (each zone holds a different group in each slot).

These expectations were worked out by hand for the v0.4 rules (2026-11-09). If the engine disagrees with one,
re-check it by hand and ask the owner before changing either the test or the rule.

| # | Day state | Request | Expected |
| --- | --- | --- | --- |
| T1 | Empty | 09:00 · CLASSIC · 10 | `ACCEPT_GOOD`; route is two letters, A plus one of B, C or D; the stay is 09:00–10:00 |
| T2 | One session 11:00 · PREMIUM with 13 | 11:00 · PREMIUM · 12 | `ACCEPT_GOOD`, placement `new`; its route never shares a zone slot with the 13-person group (their spare seats are not sold) |
| T3 | Every zone slot full: four PREMIUM sessions at each of 09:00, 11:00, 13:00, 15:00, 17:00, 19:00 | 11:00 · CLASSIC · 6 | `NOT_POSSIBLE`. Alternatives contain next day 11:00 · CLASSIC (GOOD); none on the same day |
| T4 | Empty | 09:00 · PREMIUM · 40 | `ACCEPT_GOOD`, split into 2 sessions of 20 + 20; their routes never share a zone slot |
| T5 | Empty | 20:30 · CLASSIC · 8 | `NOT_POSSIBLE` (ends 21:30). 1st alternative 20:00 · CLASSIC (GOOD); no alternative is TINGKAT |
| T6 | Three pinned PREMIUM sessions at 19:00 with routes `ABCD`, `BCDA`, `CDAB` | 19:00 · CLASSIC · 5 | `ACCEPT_COSTLY`, route `DA`, `deadMinutesDelta` 60 (B 20:00–20:30 and C 20:30–21:00 can no longer be sold, except as Tingkat). 1st alternative 19:00 · PREMIUM (GOOD, route `DABC`) |
| T7 | Full 09:00–11:00 | 10:00 · TINGKAT · 4 | `NOT_POSSIBLE`, "Zones B, C and D are in use at 10:00". 1st alternative 11:00 · TINGKAT (GOOD) |
| T8 | One pinned 09:00 · PREMIUM, route `ABCD` | 09:00 · PREMIUM · 5 | `ACCEPT_GOOD`; pinned route stays `ABCD`; new route shares no zone slot with it |
| T9 | `turnaroundMinutes` 15; one pinned 09:00 · CLASSIC, route `AD` | 09:30 · CLASSIC · 4 | Accepted with route `BA` or `CA`: A is being reset until 09:45 so the group can't start in A at 09:30, and D is in use 09:30–10:00. The reset slots A 09:30–10:00 and D 10:00–10:30 are never counted as dead |
| T10 | Any | Date 184 days ahead | `NOT_POSSIBLE`, reason mentions the 6-month window |
| T11 | Two 10:00 · CLASSIC sessions with 1 person each (routes `AD` and `DA`) | 10:00 · CLASSIC · 1 | `NOT_POSSIBLE`, "Zone A is in use 10:00–11:00" (taken zone slots are sold out whatever the group size) |
| T12 | Empty | 09:00 · PREMIUM · 26 | `ACCEPT_GOOD`, split into 13 + 13 |
| T13 | Empty | 10:00 · CLASSIC · 60 | `NOT_POSSIBLE`: 3 groups of 20 would each need zone A within the same hour, but A holds only 2 groups per hour. The reason says so and adds "Try booking part of the group at another time." Alternatives include an upgrade (GOOD); which upgrade comes first is not fixed |
| T14 | Empty | 20:30 · TINGKAT · 4 | `ACCEPT_GOOD`; route is `B`, `C` or `D` |
| T15 | Empty | 09:00 · PREMIUM_CT · 10 | Accepted; route has 4 letters, B and C are each one continuous hour, the stay is 09:00–12:00 |
| T16 | Two pinned TINGKAT sessions in D at 10:00 and 10:30 | 10:00 · CLASSIC · 10 | Accepted; route uses A plus B or C (not D) |
| T17 | Three pinned TINGKAT sessions in C at 10:00, 10:30 and 11:00 | 10:00 · CLASSIC_CT · 10 | Accepted; route uses zones A, B and D |

Plus property-based tests (Hypothesis): for random valid days, `assign` never puts two visits in one zone slot,
every route is valid for its type with no gaps between visits, fixed sessions keep their route, splits differ in
size by at most 1, and `checkEnquiry` never returns `ACCEPT_*` for a request that makes `assign` fail.

Plus a performance test: on generated fully booked days (≈ 20–30 sessions) and half-full days (the hardest case
for the search), some sessions pinned, `checkEnquiry` including alternatives completes within the §8 target on
the dev machine.

---

## 8. Non-functional

- Enquiry check returns in < 300 ms p95 (engine search budget 200 ms, see 4.1); 6-month calendar loads in < 1 s.
- No double-booking under concurrent requests (per-date lock + re-check in the transaction; test it).
- Works on desktop, tablet and the Outlook task pane width.
- Secrets only from env (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `BOOKINGS_MAILBOX`,
  `DATABASE_URL`, `GRAPH_MODE`). Provide `.env.example`.
- No real customer data in the repo (it is public): see §5.4 "Test data". The full mailbox address lives only in
  `.env` (`BOOKINGS_MAILBOX`); docs say `operations@`.
- Audit log for every booking change, with actor.
- **Backups**: laptop phase as in §5.6 (hourly, off the laptop, restore tried before go-live). After the cloud
  move, the managed database's daily backups, kept at least 30 days. Outlook's copy of future bookings (§5.4) is a
  last resort, not a backup: it has no history, audit log or settings.

---

## 9. Still open (use the defaults; do not block on these)

- Turnaround minutes between sessions → default 0, configurable.
- Do infants count toward the 25? → default: everyone counts.
- Ticket prices → default none; ranking ignores revenue.
- Deposits for far-ahead bookings → out of scope.
- Walk-ins → entered as normal bookings via the Enquiry check.
- Ticket durations → confirmed by the business on 2026-11-09 (zone-time table in §2).
- Turnaround with zone routes → setting kept at default 0 (owner, 2026-11-09); revisit if the business wants a
  reset time between groups.
- Who the M365 admin is → needed at Milestone 5 (real Graph). Until then use the fake.
- Availability calendar in `operations@` → deferred to after Milestone 5 (owner, 2026-11-10; after the live Outlook go-live since v0.7).
- Migration date (the line between history and future bookings) → default: the day the migration is committed.
- Where the app runs → decided (owner, 2026-11-11): the owner's laptop first, the cloud later (§5.5).
- Database on the laptop → decided (owner, 2026-11-11): SQLite with the safeguards in §5.5.
- Manager override to book despite a refusal → not built (Phase 2 to-do, §6). Until then, an outage clash is
  settled by changing one of the bookings.
