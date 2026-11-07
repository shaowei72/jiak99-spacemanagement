# Zone Capacity App — Build Spec (v0.3, for Claude Code)

Source of truth for building the app. Product rationale lives in the product spec doc; this file is the
implementation contract. Where this file and the doc disagree, this file wins.

---

## 1. What we are building

An engine plus thin UI that helps an F&B attraction sell and allocate its 4 identical zones.

- **Sales manager**: an enquiry arrives (date, start time, group size, ticket type). The app returns a
  verdict on the request *as asked* and up to 3 alternative pitches that use space better. One click books it.
- **Operations manager**: sees sessions on a zone timeline, auto-allocates zones, locks the day's plan.
- **Outlook (Microsoft 365) is the user layer.** Bookings and plans are mirrored into Outlook calendars via
  Microsoft Graph. The engine's database is the system of record.

There is **no hold state**. An enquiry is either booked or nothing is stored.

---

## 2. Business rules (fixed)

| Rule | Value |
| --- | --- |
| Zones | 4, identical (`A`, `B`, `C`, `D`) |
| Capacity per zone | 25 persons; adults and children each count as 1 |
| Opening hours | 09:00–21:00, every day |
| Start times | Every 30 min, 09:00–19:30 (22 start times) |
| Ticket types | `CLASSIC` Classic Hawker Experience (1.5 h), `CLASSIC_CT` Classic Hawker Experience + Chef's Table (1.5 h), `PREMIUM` Premium Hawker Experience (2 h), `PREMIUM_CT` Premium Hawker Experience + Chef's Table (3 h). Codes are stored; names are display only |
| Session must end by | 21:00 → latest start: CLASSIC / CLASSIC_CT 19:30, PREMIUM 19:00, PREMIUM_CT 18:00 |
| Zone homogeneity | One session per zone at a time; everyone in it shares start time and ticket type. Different ticket types never share a zone, even with the same length (e.g. `CLASSIC` and `CLASSIC_CT`) |
| No sharing | **Once a group takes a zone, that zone is sold out for its time slot, whatever the group size.** Spare seats are never sold to another booking; only unoccupied zones can be sold. A group over 25 takes `ceil(N / 25)` zones |
| Concurrency | All 4 zones can run at the same time, with the same or different ticket types. Nothing else (e.g. the Chef's Table) limits how many sessions run at once |
| Minimum to open a zone | None |
| Booking window | Today up to 6 months ahead (183 days) |
| Time zone | Asia/Singapore (UTC+8, no DST) |

### Configurable settings (with defaults — the business has not decided these yet)

| Setting | Default | Notes |
| --- | --- | --- |
| `turnaroundMinutes` | `0` | Zone reset time after a session; blocks the zone, not counted as a gap. Must support values like 15 |
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
Zone        { id: 'A'|'B'|'C'|'D', capacity: 25, active: boolean }
TicketType  { id: 'CLASSIC'|'CLASSIC_CT'|'PREMIUM'|'PREMIUM_CT', name, minutes: 90|90|120|180, priceAdult?, priceChild? }
Session     { id, date, start (min), type, zoneId: string|null, pinned: boolean,
              status: 'open'|'locked'|'in_progress'|'done' }
Booking     { id, sessionId, customerId, adults, children, status: 'confirmed'|'cancelled'|'no_show',
              notes, createdBy, outlookEventId?, createdAt, updatedAt }
Customer    { id, name, phone?, email?, company? }
DaySettings { date, lockedAt?: timestamp, overrides (blackouts) }
Settings    { turnaroundMinutes, smallGroupWarnBelow, alternativeDaySearch, availabilityCalendarDays, prices }
AuditLog    { id, actor, action, entity, entityId, before, after, at }
```

- A session belongs to exactly **one booking** (one group). A booking over 25 has one session per zone it takes,
  linked by a `groupRef`. `Session.headcount` = that booking's `adults + children` in this zone.
- A session with zero confirmed bookings is deleted.
- A session occupies its zone for `[start, start + minutes + turnaroundMinutes)`. Turnaround after 21:00 is
  irrelevant (clip to close).
- `zoneId` is `null` ("floating") until allocation. Sessions are **pinned** (ops fixed the zone) or **locked**
  (day plan locked) or **in progress** → their `zoneId` must never change automatically.

---

## 4. The engine (pure Python package, no I/O)

All functions are pure: `(dayState, settings, input) → result`. This is the core of the product and must be
heavily unit-tested. Standard library only (no third-party runtime dependencies).

### 4.1 Feasibility and zone assignment

`assign(sessions, settings) → { ok: true, assignment: dict[sessionId, zoneId] } | { ok: false }`

- Fixed sessions (pinned / locked / in progress / done) keep their zone.
- Floating sessions get any zone such that no two sessions in a zone overlap (including turnaround).
- Must find an assignment **if one exists**. With floating-only sessions, a greedy sweep by start time is
  sufficient; with fixed sessions, use depth-first search with backtracking (sessions sorted by start). Max
  ~88 sessions/day, 4 zones — exact search is fine. Break zone symmetry (floating sessions: try only one empty
  zone among identical empty zones) to keep it fast.
- Among valid assignments, prefer the one with **minimum dead minutes** (4.2). Use branch-and-bound on dead
  minutes, stopping when the **search budget** runs out and falling back to the best assignment found.
- **Search budget**: one budget covers a whole `checkEnquiry` call, including every alternative candidate
  (4.4), not each `assign` call separately. Default 200 ms in total, shared by all the `assign` calls it makes;
  once it is spent, the remaining calls return their first valid assignment. The budget only cuts the dead-minute optimisation short; feasibility must still be decided exactly.
  To keep the engine pure and tests deterministic, pass the budget in as a parameter with an injectable clock
  (default `time.monotonic`). This is the only clock the engine may read.

### 4.2 Dead minutes (unsellable gaps)

For a zone with an assignment, the **free gaps** are: from 09:00 to the first session, between a session's
occupancy end (incl. turnaround) and the next session's start, and from the last occupancy end to 21:00.

Before measuring gaps, round each occupancy end **up to the next 30-min grid point** (e.g. 10:45 → 11:00). The
rounding minutes are structural turnaround, never counted as dead.

A gap of length `g` minutes is **sellable** if some ticket fits inside it: there is a start time on the 30-min
grid within the gap such that `start + 90 + turnaround ≤ gap end` (or `start + 90 ≤ 21:00` for the last gap).
Otherwise the whole gap is **dead**. `deadMinutes(day)` = sum of dead gaps over all 4 zones under the best
assignment.

### 4.3 Enquiry check

```
checkEnquiry(day, settings, { date, start, type, adults, children }) → {
  verdict: 'ACCEPT_GOOD' | 'ACCEPT_COSTLY' | 'NOT_POSSIBLE',
  placement: { kind: 'new', people } | { kind: 'split', newSessions: [25, 15, …] } | null,
  deadMinutesDelta: number,      // after − before
  zoneMinutesAdded: number,      // zones taken × ticket minutes
  reason: string,                // one line, plain English, shown to sales
  alternatives: Alternative[]    // ≤ 3, see 4.4
}
```

Rules, in order:

1. **Validity**: start on the grid, `start + minutes ≤ 21:00`, date within the booking window and not a
   closed/blackout time → otherwise `NOT_POSSIBLE` with the reason (e.g. "A 2 h Premium session from 19:30 would end
   after the 21:00 close").
2. **Own zone**: the group never joins an existing session (§2 "No sharing").
3. **New session**: add a floating session and run `assign`. If infeasible → `NOT_POSSIBLE`.
   If feasible: `ACCEPT_COSTLY` when `deadMinutesDelta > 0`, or when `smallGroupWarnBelow > 0 && N <
   smallGroupWarnBelow`; otherwise `ACCEPT_GOOD`.
4. **Groups over 25** (`N > 25`): open `ceil(N / 25)` new sessions (25 each, the rest in the last one);
   feasibility and costliness as in step 3. A booking of ≤ 25 is never split.

`reason` examples (must be generated, not hard-coded):
- "Fits in a free zone with no unsellable gaps."
- "Group of 40 at 09:00 · Premium needs 2 zones (25 + 15). Fits in a free zone with no unsellable gaps."
- "Fits, but leaves 09:00–10:00 in one zone unsellable."
- "All 4 zones are in use at 11:00."

### 4.4 Alternatives

Generate candidates, evaluate each with `checkEnquiry` (without recursion into alternatives), keep only those
**strictly better** than the request's own result, rank, return the top 3.

Candidates:
1. **Shift time**: same type, start ±30 and ±60 min.
2. **Change type**: same start, other ticket types — **upgrades only, never a downgrade**. A downgrade is a lower
   level (Premium → Classic) or losing the Chef's Table; so Classic + CT ↔ Premium is a downgrade either way.
   Allowed: Classic → Classic + CT / Premium / Premium + CT; Classic + CT → Premium + CT; Premium → Premium + CT.
3. **Another day**: same start and type on dates +1, −1, +2, −2 … up to `alternativeDaySearch`, inside the booking
   window, not in the past. Only the nearest qualifying day is kept.

"Better" ordering (lexicographic):
1. Verdict: `ACCEPT_GOOD` > `ACCEPT_COSTLY` > `NOT_POSSIBLE`
2. `deadMinutesDelta` (lower is better)
3. `zoneMinutesAdded` (lower is better) — implemented as *number of zones taken*, see `DECISIONS.md`
   (needed for T5)
4. Change size (lower is better): time shift = 1 per 30 min; type change = 1; day change = 2 + |days|.
   Ties: time shift before type change before day change.
5. Revenue (higher is better) if `prices` set.

Each alternative carries `{ date, start, type, verdict, changeSummary, gainSummary }`, e.g.
`changeSummary: "Start 11:00 instead of 10:30"`, `gainSummary: "Fits with no unsellable gaps"`.

### 4.5 Availability board

`availability(day, settings, N) → for each start time × type: 'available' | 'full' | 'not_offered'`
(`available` = enough unoccupied zones can be found for the group).

### 4.6 Day metrics

| Metric | Formula |
| --- | --- |
| Seat-hour use | Σ(headcount × hours) ÷ (100 × 12) |
| Zone-hour use | Σ session hours ÷ (4 × 12) |
| Average session fill | mean(headcount ÷ 25) |
| Dead gap hours | deadMinutes ÷ 60 |
| Revenue per zone-hour | revenue ÷ Σ session hours (only if prices set) |

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
| POST | `/enquiries/check` | Sales | Body: date, start, type, adults, children → `checkEnquiry` result. Stores nothing |
| POST | `/bookings` | Sales | Book the request or an alternative. Re-runs the check **inside a transaction** with a per-date lock; returns 409 with a fresh check if it no longer fits |
| PATCH | `/bookings/:id` | Sales | Edit (re-checks) |
| POST | `/bookings/:id/cancel` | Sales | Cancel |
| POST | `/bookings/:id/no-show` | Operations | Mark no-show |
| GET | `/days/:date` | Both | Sessions, bookings, assignment, metrics |
| GET | `/availability?date&n` | Both | Availability board |
| GET | `/calendar?from&to` | Both | Per-day seat-hour use for the 6-month view |
| POST | `/days/:date/allocate` | Operations | Run `assign`, persist zones for floating sessions |
| POST | `/days/:date/lock` / `unlock` | Operations | Lock freezes zones; triggers zone-calendar write |
| PATCH | `/sessions/:id/pin` | Operations | Pin/unpin a zone |
| GET/PUT | `/settings` | Operations | Settings in section 2 |

All writes go to `AuditLog`.

### 5.4 Outlook / Microsoft Graph sync

Tenant: Microsoft 365 for business (confirmed). Use an Entra app registration with **application** permission
`Calendars.ReadWrite`, admin-consented, and restrict it to the mailboxes below using Exchange Online application
access policy / RBAC for Applications. Never request tenant-wide mail access.

Mailboxes / calendars (addresses from env vars):

| Calendar | Mailbox | Written by | Content |
| --- | --- | --- | --- |
| Bookings | shared mailbox `BOOKINGS_MAILBOX` | App, and Sales manually | One event per booking, Busy. Subject `[PREMIUM] 10:30 · 12A 3C · Tan family`. Category = ticket type colour. Booking id stored in an open extension |
| Availability | same shared mailbox, calendar "Availability" | App only | One event per start time with something sellable, next `availabilityCalendarDays` days, e.g. "10:30 · 2 zones free · Classic, Premium" |
| Zone A–D | room mailboxes `ZONE_A_MAILBOX` … | App only, after lock | One event per session: subject "Premium · 18/25", body lists bookings |

Sync rules:
- **App → Outlook**: on every booking/session change, upsert the affected events (idempotent by stored ids).
  Availability calendar: debounce, rewrite affected dates within 60 s.
- **Outlook → App** (manual entries by sales in the Bookings calendar): Graph change notifications on the
  Bookings calendar, plus a delta-query sweep every 5 min as a fallback. Renew subscriptions before expiry.
  - Event without our extension → parse the subject with the template parser:
    `^\[(classic|classic[ _+-]?ct|premium|premium[ _+-]?ct)\]\s*(\d{1,2}:\d{2})\s*[·|\-]\s*(\d+)A(?:\s*(\d+)C)?\s*[·|\-]\s*(.+)$`
    (case-insensitive, tolerant of spacing; `[Classic CT]`, `[classic-ct]`, `[CLASSIC_CT]` all mean `CLASSIC_CT`).
    Date = event date.
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
4. **Outlook sync** — `packages/graph` against the fake first; then real Graph behind env flag `GRAPH_MODE=real`.
   Import command.
5. **Outlook add-in** — task pane hosting the Enquiry check page; "Book" creates the booking via the API (which
   writes the Outlook event).
6. **Phase 2 (only when asked)** — consolidation suggestions, day-of changes (walk-ins, late arrivals), run sheet,
   metrics dashboard.

---

## 7. Engine acceptance tests (must pass)

Defaults: turnaround 0, `smallGroupWarnBelow` 0. "Empty" = no sessions. Times local. Durations: CLASSIC 1.5 h,
PREMIUM 2 h, PREMIUM_CT 3 h (these tests were written for 1.5 / 2 / 3 h tickets and keep those durations).

| # | Day state | Request | Expected |
| --- | --- | --- | --- |
| T1 | Empty | 10:00 · PREMIUM · 10 | `ACCEPT_COSTLY`, `deadMinutesDelta` 60 (09:00–10:00 unsellable). Alternatives: 1st 10:30 · PREMIUM (GOOD), 2nd 09:00 · PREMIUM (GOOD) |
| T2 | One session 11:00 · PREMIUM with 13 | 11:00 · PREMIUM · 12 | `ACCEPT_GOOD`, placement `new` (the 13-person zone is sold out; its 12 spare seats are not sold) |
| T3 | Four sessions 10:00 · PREMIUM_CT, each 25 | 11:00 · CLASSIC · 6 | `NOT_POSSIBLE`. Alternatives contain next day 11:00 · CLASSIC (GOOD); none on the same day |
| T4 | Empty | 09:00 · PREMIUM · 40 | Feasible, 2 new sessions (25 + 15), `ACCEPT_GOOD` |
| T5 | Empty | 19:30 · PREMIUM · 8 | `NOT_POSSIBLE` (ends 21:30). 1st alternative 19:00 · PREMIUM (GOOD); no Classic option (that would be a downgrade) |
| T6 | Four sessions 09:00 · PREMIUM (full) | 11:30 · PREMIUM · 10 | `ACCEPT_COSTLY` (30 min dead gap 11:00–11:30). 1st alternative 11:00 · PREMIUM (GOOD) |
| T7 | Four sessions 09:00 · PREMIUM (full) | 11:00 · PREMIUM_CT · 10 | `ACCEPT_GOOD` |
| T8 | Zone A pinned 09:00 · PREMIUM_CT; three floating 09:00 · PREMIUM | 11:00 · PREMIUM · 5 | Feasible; assignment keeps A for the pinned session; new session in a zone freed at 11:00 |
| T9 | `turnaroundMinutes` 15; four sessions 09:00 · CLASSIC (full) | 10:30 · CLASSIC · 4 | `NOT_POSSIBLE` (zones busy until 10:45). 1st alternative 11:00 · CLASSIC (`ACCEPT_GOOD`; the 10:45–11:00 rounding is not dead time) |
| T10 | Any | Date 184 days ahead | `NOT_POSSIBLE`, reason mentions the 6-month window |
| T11 | Four sessions 10:00 · PREMIUM with 1 person each | 10:00 · PREMIUM · 1 | `NOT_POSSIBLE`, "All 4 zones are in use at 10:00" (taken zones are sold out whatever their size) |

Plus property-based tests (Hypothesis): for random valid days, `assign` never overlaps sessions in a zone, never
moves fixed sessions, and `checkEnquiry` never returns `ACCEPT_*` for a request that makes `assign` fail.

Plus a performance test: on generated fully booked days (≈ 20–30 sessions) and half-full days (the hardest case
for the search), some sessions pinned, `checkEnquiry` including alternatives completes within the §8 target on
the dev machine.

---

## 8. Non-functional

- Enquiry check returns in < 300 ms p95 (engine search budget 200 ms, see 4.1); 6-month calendar loads in < 1 s.
- No double-booking under concurrent requests (per-date lock + re-check in the transaction; test it).
- Works on desktop, tablet and the Outlook task pane width.
- Secrets only from env (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `BOOKINGS_MAILBOX`,
  `ZONE_A_MAILBOX`…`ZONE_D_MAILBOX`, `DATABASE_URL`, `GRAPH_MODE`). Provide `.env.example`.
- Audit log for every booking change, with actor.

---

## 9. Still open (use the defaults; do not block on these)

- Turnaround minutes between sessions → default 0, configurable.
- Do infants count toward the 25? → default: everyone counts.
- Ticket prices → default none; ranking ignores revenue.
- Deposits for far-ahead bookings → out of scope.
- Walk-ins → entered as normal bookings via the Enquiry check.
- Ticket durations (Classic + CT 1.5 h, Premium + CT 3 h) → as given by the business on 2026-11-07; confirm.
- Who the M365 admin is → needed only at Milestone 4 (real Graph). Until then use the fake.
