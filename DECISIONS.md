# Decisions

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-11-06 | Engine database is the system of record; Outlook mirrors it | Outlook can't enforce capacity or prevent double-booking |
| 2026-11-06 | Zones float until ops locks the day | Identical zones; maximises what sales can sell |
| 2026-11-06 | No hold state | Business decision: enquiry is booked or not |
| 2026-11-06 | Last intake 19:30; close 21:00 | Business decision |
| 2026-11-06 | ~~Separate groups may share a session (same start + type, ≤ 25 total)~~ — superseded 2026-11-07, see below; no minimum group size still applies | Business decision |
| 2026-11-07 | Python 3.13 + uv instead of TypeScript + pnpm | Owner is learning Python and must be able to read and review the code |
| 2026-11-07 | Web UI server-rendered (FastAPI + Jinja2 + HTMX) instead of React SPA | Less JavaScript; Outlook task pane can load the pages directly |
| 2026-11-07 | Outlook add-in stays a thin HTML/TypeScript shell | Office add-ins require Office.js; logic lives in the Python API |
| 2026-11-07 | One 200 ms search budget per `checkEnquiry` instead of 50 ms per `assign` | ~25 candidate checks × 50 ms in Python would break the 300 ms p95 target |
| 2026-11-07 | No generic scheduling engine or OR-Tools for now | Not in scope; extract a generic core only when a second use case exists |
| 2026-11-07 | §4.4 criterion 3 compares the number of new sessions, not zone minutes | Acceptance test T5 ranks 19:00 · 2 h above 19:30 · 1.5 h; raw zone minutes would always push shorter tickets |
| 2026-11-07 | "Strictly better" alternative = better on verdict, then dead minutes, then new sessions | Change size is 0 for the request itself, so including it would rule out every alternative |
| 2026-11-07 | Final ranking tie-breaks: nearer day, earlier start, shorter ticket | Makes results deterministic; T1 expects 09:00 before 11:00 |
| 2026-11-07 | `assign` is a memoised exact search (DP) instead of plain branch-and-bound | Same optimum, much faster; p95 enquiry check measured at ≈ 16 ms |
| 2026-11-07 | Blackouts apply to all zones and block turnaround too; gaps before a blackout need no turnaround | Spec silent; simplest consistent rule |
| 2026-11-07 | `today` (and optionally the current minute) are passed into the engine | Engine never reads the calendar; booking window and "past" checks stay testable |
| 2026-11-07 | Engine `Session` carries adults/children totals, not individual bookings | Engine only needs headcount and revenue; the API sums confirmed bookings |
| 2026-11-07 | Added `apps/cli` (`uv run check …`) for MVP 0 | Lets sales try real enquiries before any screens exist |
| 2026-11-07 | 4 ticket types replace T90/T120/T180: CLASSIC 1.5 h, CLASSIC_CT 1.5 h, PREMIUM 2 h, PREMIUM_CT 3 h | Business decision (owner) |
| 2026-11-07 | Ticket types use short stable codes; full names are display-only | Renaming a product mustn't break stored bookings or Outlook parsing; same-length types need distinct labels |
| 2026-11-07 | Acceptance tests T1–T10 map T90→CLASSIC, T120→PREMIUM, T180→PREMIUM_CT | Keeps every test's original durations and expectations |
| 2026-11-07 | Equal alternatives: type without Chef's Table first (TICKET_TYPES order) | CLASSIC and CLASSIC_CT have equal length, so length alone can't break the tie |
| 2026-11-07 | Different ticket types never share a zone in the same time slot, even with equal length | Business decision (owner); closes the CLASSIC / CLASSIC_CT sharing question |
| 2026-11-07 | Outlook subject tag is the ticket code, e.g. `[PREMIUM]`, `[Classic CT]` | Duration tags (`[1.5h]`) can't tell Classic from Classic + Chef's Table |
| 2026-11-07 | **No sharing: a zone taken by a group is sold out for its time slot, whatever the group size.** Only unoccupied zones can be sold | Business decision (owner); replaces the 2026-11-06 sharing rule |
| 2026-11-07 | Joining removed from the engine (join placement, "join nearby" alternatives, board "join" status) rather than kept behind a setting | Keeps the code simple to read; no dead options |
| 2026-11-07 | Acceptance test T2 changed to expect a new zone; T11 added (four 1-person groups sell out a slot) | Follows the no-sharing rule |
| 2026-11-07 | Small-group warning stays off (`smallGroupWarnBelow` = 0) | Business decision (owner): leave off for now |
| 2026-11-07 | Alternatives never suggest a downgrade: lower level or losing the Chef's Table (so Classic + CT ↔ Premium both count) | Business decision (owner); strict reading chosen for the one ambiguous pair |
| 2026-11-07 | Acceptance test T5 no longer expects "19:30 Classic" as an alternative | It is a downgrade from Premium |
| 2026-11-07 | All 4 zones may run concurrently with any mix of ticket types; no Chef's Table capacity limit | Business decision (owner) |
| 2026-11-07 | Sessions point to their booking (`sessions.booking_id`) instead of `Booking.sessionId` + `groupRef` | A booking over 25 has several sessions; one foreign key says that directly |
| 2026-11-07 | Groups over 25 are split with adults filling zones first (30A 10C → 25A + 5A 10C) | Spec silent; any split is valid since everyone counts as 1 |
| 2026-11-07 | Dev sign-in stand-in: `X-User` / `X-Role` headers until Entra ID sign-in is added | Lets Milestones 2–3 be built and tested without a tenant; routes won't change |
| 2026-11-07 | Booking lock: PostgreSQL `pg_advisory_xact_lock(YYYYMMDD)`; SQLite uses an in-process lock | SQLite is for single-process local dev only; the PostgreSQL lock works across processes |
| 2026-11-07 | Every booking creates a new customer row (no matching of repeat customers yet) | Keeps M2 simple; matching can be added when the Outlook import shows real data |
| 2026-11-07 | Enquiry check is `GET /api/enquiries/check?…` instead of POST | It reads and stores nothing; inputs are 5 simple values; links can be shared. Sent with `Cache-Control: no-store` so a stale answer is never reused |
| 2026-11-08 | Edit re-checks only when date, time, type or headcount change, ignoring the booking's own sessions | A booking must not clash with itself; notes/customer edits need no check and work even after the visit |
| 2026-11-08 | An edit keeps the session (and its zone) if only the headcount changes and the zone count stays the same; otherwise new floating sessions | Keeps ops' zone plan when nothing about the slot moved; anything else is a new slot |
| 2026-11-08 | Moving a booking to another date locks both dates, in calendar order | Two crossing moves can never deadlock |
| 2026-11-08 | No-show only once the start time is reached; before that the answer is "cancel" | Stops a mistaken no-show from freeing a zone early; spec silent |
| 2026-11-08 | No-show deletes the booking's sessions (zone freed), the booking stays in the day view | SPEC §3: a session with zero confirmed bookings is deleted; ops still sees who didn't come |
| 2026-11-08 | Audit snapshots include the customer details | So customer-only edits are recorded too |
| 2026-11-08 | `uv run check` prints a zone board (`--board operations`, default) as well as the sales board (`--board sales`) | Zones A–D are what ops thinks in; it shows the engine's current best plan with the request added, so "costly" gaps are visible |
| 2026-11-08 | Short labels are CHE, CHE+CT, PHE, PHE+CT (`TICKET_SHORT_NAMES`); codes unchanged | Business feedback; codes stay stable so stored bookings are unaffected; the CLI also accepts labels as input |
| 2026-11-08 | Seed script (`uv run seed`) loads the CLI day files, one booking per session; `apps/api` depends on `jiak-cli` for the file reader | One day-file format for CLI and database; a test checks all 29 scenarios answer the same from the database |
| 2026-11-08 | Seeding refuses booked dates without `--replace`, and non-SQLite databases without `--yes` | Sample data must never overwrite real bookings or reach production by accident |
| 2026-11-08 | `/calendar` also returns bookings, people, zone-hour use, closed and locked per day (SPEC asks only seat-hour use) | The calendar page needs them for tooltips and greying out closed days; costs no extra queries per day |
| 2026-11-08 | `/calendar` skips the zone plan (no dead-gap figure) and allows at most 366 days per request | 180 zone plans would break the < 1 s target; dead gaps belong to the single-day view |
| 2026-11-08 | `/availability` takes `n` from 1 to 100 and returns the ticket labels and lengths with the board | 100 = all 4 zones full; pages then never hard-code labels |
| 2026-11-08 | Allocated zones are a suggestion: unpinned sessions on an unlocked day may still move when the plan changes | Keeps zones flexible (CLAUDE.md); only pins and the day lock freeze a zone, as SPEC §3 lists |
| 2026-11-08 | Lock first allocates, then freezes every session; sessions booked after the lock are frozen at the next allocate | Every session of a locked day ends up with a fixed zone |
| 2026-11-08 | Pinning re-plans and stores zones for the other movable sessions; refused (409) if no valid plan is left | Stored zones never show two groups in one zone at the same time |
| 2026-11-08 | `PATCH /sessions/:id/pin` body: `{"zone": "B"}`, `{}` (current zone) or `{"pinned": false}`; plan actions return the day view | One route for pin/unpin as SPEC lists; the page needs the whole day as other groups may move |
| 2026-11-08 | Zone-calendar write on lock deferred to Milestone 4 | Needs the Graph gateway (`packages/graph`), built in Milestone 4 |
| 2026-11-08 | `PUT /settings` is refused (409) if any booked day from today on would have no valid zone plan; past days are ignored | A settings change must never strand confirmed bookings |
| 2026-11-08 | `PUT /settings` doesn't take every date lock; a booking racing a settings change could slip through | Settings change rarely and only by ops; locking ~180 dates per change isn't worth it yet |
| 2026-11-08 | Settings limits: turnaround 0–120 min, small-group warning 0–25, alternative days 0–30, Outlook horizon 1–183 days | Catches typos; wide enough for any real choice |
| 2026-11-08 | Per-date overrides via `PUT /days/:date/settings` (closed + blackouts, within 09:00–21:00); refused if they overlap bookings | SPEC §2 lists them as settings but §5.3 has no route; same "don't strand bookings" rule |
| 2026-11-08 | `GET /settings` is open to Sales too | Sales sees the turnaround and prices the answers depend on; only ops can change them |
| 2026-11-08 | Docker: one `Dockerfile` (python:3.13-slim + uv) at the repo root; the api runs `alembic upgrade head` on every start | The api needs the whole uv workspace (engine, CLI day files for `seed`); tables always match the code |
| 2026-11-08 | PostgreSQL 17 image; a second database `jiak_test` is created on first start for `TEST_DATABASE_URL` | Tests wipe their database, so they must never share the dev one |
| 2026-11-08 | Container ports published on 127.0.0.1 only; URLs use `127.0.0.1`, not `localhost` | Not reachable from the network; on Windows `localhost` tries IPv6 first and the connection hangs |
| 2026-11-08 | HTMX 2.0.4 is kept in the repo (`static/vendor/htmx.min.js`), not loaded from a CDN | Pages work offline and inside the Outlook pane without reaching a third-party site |
| 2026-11-08 | Web pages use the same dev `X-Role` stand-in as the API; a browser sends no header, so it acts as Operations | Entra ID sign-in replaces it later for pages and API alike |
| 2026-11-08 | Enquiry check form also works without JavaScript (plain GET, whole page); with HTMX only the result is swapped and the URL updated | Shareable links; nothing breaks if the script fails to load |
| 2026-11-08 | Booking from the page posts an HTML form to `POST /enquiry/book`, which calls the same booking service as `POST /api/bookings` (same date lock and re-check) | Works without JavaScript and inside the Outlook pane; one place holds the booking rules |
| 2026-11-08 | Without JavaScript a successful booking redirects (303) to `/bookings/:id`; with HTMX the confirmation is swapped in and the URL set to the same page | Reloading the page can't book the group twice; the booking has a link to share |
| 2026-11-08 | The Book form lets sales adjust adults and children before booking; the date, time and ticket come from the button | Headcounts often change during the call; the server re-checks everything on submit anyway |
| 2026-11-08 | HTMX is configured (`htmx-config` meta) to show 409 "no longer fits" and 422 form mistakes; other errors still aren't swapped in | Both carry a message the user needs; keeps the real HTTP status codes |
| 2026-11-08 | Empty phone, email or company fields are stored as "not given" (null), not as empty text | Same as leaving them out in the JSON API |
| 2026-11-08 | A Book button on an out-of-date result says "No longer fits" (like a lost race on the open form), not "Not possible" | To sales both mean "it fitted when I looked, now it doesn't"; "Not possible" stays the answer to Check (owner's call) |
