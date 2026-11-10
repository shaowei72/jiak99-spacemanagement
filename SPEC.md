# Zone Capacity App — Build Spec (v0.4, for Claude Code)

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
```

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
  dev, but the concurrency tests in §8 must run against PostgreSQL). Per-date booking lock: PostgreSQL
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
  needed only for this, at Milestone 5.
- Docker Compose for local dev (api + db).

### 5.2 Repo layout

```
/packages/engine      domain types, assign, dead_minutes, check_enquiry, alternatives, availability, metrics
/packages/graph       GraphGateway protocol + real + fake implementations, subject-template parser
/apps/api             JSON routes, HTML pages (templates/, static/), persistence, migrations, sync jobs, auth,
                      import-outlook command
/apps/outlook-addin   manifest + task pane shell (Milestone 5)
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
access policy / RBAC for Applications. Never request tenant-wide mail access.

Mailboxes / calendars (addresses from env vars):

| Calendar | Mailbox | Written by | Content |
| --- | --- | --- | --- |
| Bookings | shared mailbox `BOOKINGS_MAILBOX` | App, and Sales manually | One event per booking, Busy, covering the whole stay. Subject `PHE+CT · Tan family · 18 pax · ABDC · 11:00–14:00` (short name · customer · people · route · times). A split group lists every sub-group's route: `PHE · Lee wedding · 40 pax · ABCD/DCBA · 10:00–12:00`. Category = ticket type colour. Booking id stored in an open extension |
| Availability | same shared mailbox, calendar "Availability" | App only | One event per start time with something sellable, next `availabilityCalendarDays` days, e.g. "10:30 · CHE, CHE+CT, PHE" |

There are **no per-zone calendars** (no room mailboxes): zone assignments are not shown in Outlook beyond the
route in the booking subject.

The route in the subject is the **current** plan. Until the day is locked it may change as other bookings
arrive; the app then updates the subject (same as any other booking change).

Sync rules:
- **App → Outlook**: on every booking/session change, upsert the affected events (idempotent by stored ids).
  Availability calendar: debounce, rewrite affected dates within 60 s.
- **Outlook → App** (manual entries by sales in the Bookings calendar): Graph change notifications on the
  Bookings calendar, plus a delta-query sweep every 5 min as a fallback. Renew subscriptions before expiry.
  - Event without our extension → parse the subject with the template parser:
    `^\[(tingkat|classic|classic[ _+-]?ct|premium|premium[ _+-]?ct)\]\s*(\d{1,2}:\d{2})\s*[·|\-]\s*(\d+)A(?:\s*(\d+)C)?\s*[·|\-]\s*(.+)$`
    (case-insensitive, tolerant of spacing; `[Classic CT]`, `[classic-ct]`, `[CLASSIC_CT]` all mean `CLASSIC_CT`).
    Date = event date. Sales keep typing this format; the app then rewrites the subject into the format above.
  - Parsed and fits → create booking, attach extension, normalise the event's times/subject.
  - Parsed but does not fit → do not create; set category "Conflict" and email the creator with the check
    result and alternatives.
  - Unparseable → category "Needs format", email the creator with the expected format.
  - Event with our extension edited or deleted in Outlook → treat as edit / cancel (re-check edits).
- One-off **import** command: read existing Bookings events from today to +6 months, run the parser, output a
  CSV report of OK / Conflict / Needs format before committing (`--dry-run` default).

---

## 6. Milestones (build in this order; each ends green)

1. **Engine** — section 4 complete with the acceptance tests in section 7. No I/O.
2. **API + DB** — section 5.3 with SQLAlchemy models + Alembic migrations, seed script (sample days),
   transaction-safe booking.
3. **Web UI** (Jinja2 + HTMX pages served by `apps/api`) — Enquiry check (verdict, reason, 3 alternatives, Book buttons), Availability board, 6-month
   calendar (colour by seat-hour use), Ops timeline (zones × time, dead gaps shaded, pin, allocate, lock), Settings.
3R. **Zone routes (v0.4 rules)** — rework Milestones 1–3 for §2 v0.4: engine (routes, Tingkat, even splits,
   dead zone slots, new §7 tests), database migration (`zone` → `route`), example days and scenarios, CLI boards,
   API and pages (timeline shows each group's visits across zones). Uses example days only; no real data yet.
4. **Outlook sync** — `packages/graph` against the fake first; then real Graph behind env flag `GRAPH_MODE=real`.
   Import command.
5. **Outlook add-in** — task pane hosting the Enquiry check page; "Book" creates the booking via the API (which
   writes the Outlook event).
6. **Phase 2 (only when asked)** — consolidation suggestions, day-of changes (walk-ins, late arrivals), run sheet,
   metrics dashboard. **To do:** suggest splitting a large group across start times (e.g. 60 · CHE → 30 at 10:00 +
   30 at 11:00), and bookings with more than one start time. Until then sales book each part as its own booking.

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
- Audit log for every booking change, with actor.

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
- Who the M365 admin is → needed only at Milestone 4 (real Graph). Until then use the fake.
