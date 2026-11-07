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
