# Architecture: a map of the app

A high-level overview for a developer joining the project (state on 2026-11-11: Milestones 1–4L done, SPEC v0.7).
`SPEC.md` is the contract, `DECISIONS.md` says why things are as they are, `docs/ENGINE.md` explains the route
planner, `docs/LAPTOP-INSTALL.md` how it runs on the owner's laptop.

## In one paragraph

Jiak99 is a **Python 3.13 monorepo** (a uv workspace) with four parts:

- a **pure engine** that knows the business rules;
- a **calendar package** that knows how Outlook events look;
- a **web app** (FastAPI) that puts them together with a database, web pages and background jobs;
- a small **command-line tool**.

Dependencies only point inward: the engine knows nothing about the others, so it can be tested on its own.

## Layers and dependencies

```
                    ┌───────────────────────────────────────────────────────────┐
  Browser  ───────▶ │ apps/api  (jiak_api)  FastAPI app: web pages, JSON API,   │ ──▶ Database
  (127.0.0.1:8000)  │ database, background jobs, admin commands                 │     SQLite (laptop) /
                    └──────┬──────────────────────┬─────────────────┬───────────┘     PostgreSQL (Docker, cloud)
                           │ uses                 │ uses            │ uses (day files for seed)
                           ▼                      ▼                 ▼
                ┌────────────────────┐   ┌──────────────────┐  ┌──────────────────┐
                │ packages/graph     │   │                  │  │ apps/cli         │
                │ Outlook events:    │──▶│ packages/engine  │◀─│ check, scenarios │
                │ format, readers,   │   │ business rules,  │  └──────────────────┘
                │ gateway (fake/real)│   │ pure functions   │
                └─────────┬──────────┘   └──────────────────┘
                          │ GraphGateway protocol
                          ▼
              Outlook: operations@ calendar (Microsoft Graph, Milestone 5; in-memory fake until then)
```

## The four parts

| Part | What it is | Key modules | Rules |
|---|---|---|---|
| **`packages/engine`** | The brain: can this group fit, which route through zones A–D, what's wasted, what to offer instead | `model` (data types), `rules` (tickets, zone sets), `assign` (route planner), `evaluate` / `enquiry` (the check), `alternatives`, `availability`, `metrics`, `timeline` | Standard library only, no I/O, no `datetime` (minutes + `YYYY-MM-DD` strings), `mypy --strict`, 100% test coverage |
| **`packages/graph`** | Everything about calendar events, without a database | `model` (`CalendarEvent`), `gateway` (the `GraphGateway` protocol), `fake` (in-memory calendar), `format` (subject, body, notes, the "Before the app" divider), `template` (hand-typed `[PHE] 11:00 · 10A · Tan`), `oldstyle` + `ics` + `takeover` + `sheet` + `carryover` (one-off take-over; `carryover` copies corrections from an earlier review sheet into a fresh one), `anonymise` | Depends on the engine only; 100% coverage. The real Microsoft Graph gateway comes in Milestone 5 |
| **`apps/api`** | The application: web pages, API, database, Outlook jobs, admin commands | see the next table | FastAPI + Jinja2/HTMX pages, SQLAlchemy 2 + Alembic |
| **`apps/cli`** | Try the engine from a terminal | `check` (one enquiry), `scenarios` (the scenario week) | Uses the engine only |

## Inside `apps/api`

| Group | Modules | Job |
|---|---|---|
| **Entry point** | `main` | Builds the app; starts the background jobs (Outlook sweep every 5 min, hourly backup) |
| **Web pages** | `pages` (Check and book), `booking_pages`, `ops_pages` (Day plan, Calendar, Settings), `restore_pages`, `templates/` | Server-rendered HTML with HTMX |
| **JSON API** | `routes` (`/api/...`), `schemas` (Pydantic) | The same actions for programs (`/docs` lists them) |
| **Business actions** | `bookings` (book, edit, cancel, no-show, each under a per-date lock), `plan` (allocate, lock, pin), `schedule` (database → engine input), `overview`, `business_settings` | Call the engine, write the result, record the audit |
| **Data** | `models` (tables), `db` (connection, per-date lock, SQLite safeguards), `migrate` + `migrations/`, `audit` | |
| **Outlook** | `outlook_sync` (app → Outlook after every change), `outlook_inbound` (the sweep: Outlook → app), `outlook_health` (banner + `/api/health`) | Talk to Outlook only through `GraphGateway` |
| **One-off take-over** | `import_outlook`, `take_over` | Reads the old calendar and turns it into bookings and history |
| **Laptop safety** | `backup`, `restore` | Hourly backups, restore with Outlook sync paused |
| **Plumbing** | `config` (settings from the environment), `auth` (no sign-in yet: `JIAK_USER`), `clock` (Singapore time), `seed`, `anonymise_ics` | |

## Two flows worth explaining

**Booking from the web page:**

```
Check and book page ─▶ bookings.create_booking
    ├─ per-date lock (SQLite write lock / PostgreSQL advisory lock): no double-booking
    ├─ schedule loads the day ─▶ engine checks it (fits? which route? alternatives)
    ├─ save booking + sessions + audit entry, commit
    └─ note the date as "waiting"  ─▶  after the reply: outlook_sync writes the Outlook event
```

**Outlook → app (the sweep, every 5 minutes):**

```
write anything still waiting ─▶ read today … +6 months from Outlook
    ├─ app's events changed in Outlook  ─▶ re-check like an edit; if it doesn't fit, put back with a note
    ├─ app's events deleted             ─▶ cancel the booking (never more than 5 at once)
    └─ hand-typed entries (oldest first) ─▶ book, or mark "Conflict" / "Needs format"
```

## Data (main tables)

- **Bookings:** `customers`, `bookings` (with the link to the Outlook event), `sessions` (one per sub-group, holding
  the route), `day_settings` (closed days, blackouts, lock), `app_settings`, `audit_log`.
- **Take-over history:** `past_bookings`.
- **Outlook link state:** `outlook_state` (incl. "paused after restore"), `outlook_pending` (dates waiting),
  `outlook_flags` (marked hand-typed entries).
- **Backups:** `backup_state`.

## Commands

| Command | Part | Does |
|---|---|---|
| `uv run check`, `uv run scenarios` | cli | Try enquiries against example days |
| `uv run seed` | api | Load example days into the database |
| `uv run import-outlook` | api | The one-off calendar take-over (dry run, `--carry-over` from an earlier sheet, rehearsal, commit) |
| `uv run anonymise-ics` | api | Make an anonymised test copy of a calendar file |
| `uv run backup`, `uv run restore` | api | Back up now; put a backup back |
| `scripts\windows\*.ps1` | — | The owner's laptop: setup, start, stop, auto-start, update |

## Where it runs

- **Now (laptop phase):** the owner's laptop, SQLite in `%LOCALAPPDATA%\Jiak99`, reachable from that laptop only.
  Started at sign-in by `scripts\windows\*.ps1`; hourly backups to OneDrive for Business.
- **Development:** Docker Compose (app + PostgreSQL 17). GitHub Actions tests every push on SQLite and PostgreSQL.
- **Later (Milestone 6, the cloud move):** the same code on a server with HTTPS, Microsoft sign-in and managed
  PostgreSQL.
