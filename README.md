# Jiak99 zone capacity app

> **Running a demo on another PC? Follow [DEMO.md](DEMO.md).**

Helps sales and operations sell Jiak99's 4 zones (up to 25 people each): A Tour/Exhibit, B Dining,
C Chef's Table / Dining and D Sandart. A group moves through several zones during its stay, 30 min at a time,
back to back; the order of the zones (its **route**, e.g. `ABDC`) is planned by the app. A 30-min zone slot holds
one group, however few people are in it. Groups over 25 are split evenly into sub-groups with their own routes.

| Code | Label | Ticket | Zones (the app picks one set and the order) | Length |
| --- | --- | --- | --- | --- |
| `TINGKAT` | Tingkat | Tingkat Meals | B, C or D | 30 min |
| `CLASSIC` | CHE | Classic Hawker Experience | A + one of B, C, D | 1 h |
| `CLASSIC_CT` | CHE+CT | Classic Hawker Experience + Chef's Table | A + D + one of B, C | 1.5 h |
| `PREMIUM` | PHE | Premium Hawker Experience | A, B, C, D | 2 h |
| `PREMIUM_CT` | PHE+CT | Premium Hawker Experience + Chef's Table | A 30 min, B 1 h, C 1 h, D 30 min | 3 h |

Codes are what the database and API store; labels and names are display-only (`engine/rules.py`), so they can be
renamed without touching data.

`SPEC.md` is the contract, `DECISIONS.md` records deviations, `CLAUDE.md` has working rules. How the engine plans
routes and judges waste: [docs/ENGINE.md](docs/ENGINE.md). Why the laptop runs SQLite:
[docs/DATABASE-CHOICE.md](docs/DATABASE-CHOICE.md). Installing on the owner's laptop: [docs/LAPTOP-INSTALL.md](docs/LAPTOP-INSTALL.md).

## Status

Done (SPEC v0.7 §6 has the full list): Milestone 1 (the engine, `packages/engine`, plus a command-line tool),
Milestone 2 (API + database), Milestone 3 (web pages: Check and book, Bookings, Day plan, 6-month calendar,
settings), Milestone 3R (the zone-route rules of SPEC v0.4), Milestone 4 (Outlook sync against the fake calendar:
app → Outlook, the one-off take-over, Outlook → app with the fail-back, the warning banner and `/api/health`) and
Milestone 4L (the owner's laptop: SQLite safeguards, backups and restore, Windows scripts, tests on GitHub).
Milestones 4 and 4L were merged into `main` on 2026-11-11.

Next: Milestone 5, live Outlook and go-live: the M365 admin's setup, the real Microsoft Graph connection (tested on
a test mailbox first), the completed review sheet and the real take-over.

**After pulling Milestone 3R:** run `uv run alembic upgrade head`. Stored zones can't become routes, so every
existing group becomes unplanned (no route, not pinned) until the day is allocated again. Old example data may not
fit the new rules any more; reload it with `uv run seed week --replace` and `uv run seed --replace`.

## Setup

Install [uv](https://docs.astral.sh/uv/), then:

```
uv sync          # installs Python 3.13, the packages and dev tools
uv run pytest    # all tests
```

## Try an enquiry

```
uv run check 2026-12-01 11:30 PREMIUM 10                 # busy-day.json + operations board (defaults)
uv run check 2026-12-01 11:30 PREMIUM 10 --board sales   # what can still be sold
uv run check 2026-12-01 10:00 PREMIUM 10 --empty-day     # start from an empty day
uv run check 2026-12-01 10:30 classic-ct 6 --children 2 --no-board
uv run check 2026-12-01 10:30 CLASSIC 4 --day apps/cli/examples/week.json
uv run check --help
```

By default the day comes from `apps/cli/examples/busy-day.json`, which only has sessions on 2026-12-01 (other dates
are empty), and the board is printed. `--day` and `--empty-day` can't be used together.

Two boards (`--no-board` hides them):

- `--board operations` (default): zones A–D across, time down. Each cell shows which group is in that zone
  (its number in the day file and its ticket) under the engine's current best plan, with the request added and
  marked `*`. A list under the board gives each group's size and route. Only pinned routes are fixed; the rest
  may still change until ops allocates the day.
- `--board sales`: start times down, ticket types across. `+` = a group this size can still be sold.

Ticket types can be typed as the label (`PHE`, `che+ct`) or the code, in any case, with `_`, `-` or a space
(`classic-ct`, `Classic CT`).

Write a day file in the same format as `apps/cli/examples/week.json` to try real days.

## Scenario week

`uv run scenarios` checks 31 enquiries against an 8-day test week and compares each answer with the expected
one. How the scenarios were built and what each one tests: `apps/cli/examples/SCENARIOS.md`.

## API and database

The database is `DATABASE_URL` (default: the SQLite file `jiak99-dev.db` in the repo root). Its tables are created
and changed only by Alembic migrations in `apps/api/src/jiak_api/migrations/versions/`.

```
uv run alembic upgrade head                      # create or update the tables (run after every pull)
uv run uvicorn jiak_api.main:app --reload        # dev server; try the routes at http://127.0.0.1:8000/docs
```

Web pages: http://127.0.0.1:8000/ opens **Check and book**. Under each answer it lists every start time that still
fits the group that day, per ticket (leave Start empty to see only those times). A good or costly
verdict and each alternative has a **Book** button: fill in the customer's name (phone, email, company and notes
are optional) and press Book. If the slot filled up after the check (when pressing either Book button), the page
says **No longer fits** and shows fresh alternatives; "Not possible" is only the answer to Check. Each booking has its own page, `/bookings/<id>`.

The other pages (top menu): **Bookings** (`/bookings`, the list from today on, filter by dates and customer;
each booking can be edited, cancelled or marked no-show), **Day plan** (`/ops`, zones × time: each group's visits are numbered bars, unsellable slots hatched; allocate, lock, pin a route, closed day and
blocked times), **Calendar** (`/calendar`, six months coloured by seat-hour use) and **Settings** (`/settings`). The
full availability grid is still at `/board`, without a menu link.

The same data as JSON, once the server runs (after `uv run seed`):

- http://127.0.0.1:8000/api/availability?date=2026-12-01&n=10 — the sales board for a group of 10
- http://127.0.0.1:8000/api/calendar?from=2026-12-01&to=2027-05-31 — per-day use for the 6-month calendar
- http://127.0.0.1:8000/api/days/2026-12-01 — one day: bookings, routes and metrics

Changes (book, edit, cancel, no-show, allocate, lock/unlock, pin) are easiest to try in `/docs`. Leave `x-role`
empty to act as Operations, or type `Sales`.

### Sample data

The API only sees bookings stored in the database, not the JSON example days that `uv run check` reads. To give
the API the same data, load a day file into the database:

```
uv run seed                  # busy-day.json: then the API answers exactly like `uv run check`
uv run seed week             # the scenario week (week.json), incl. a closed day and a blackout
uv run seed week --replace   # wipe those dates first (also removes real bookings on them)
```

Each session becomes one booking ("Sample group N"), keeping its route and pin. Seeding refuses dates that already
have bookings unless you pass `--replace`, and refuses any non-SQLite database unless you pass `--yes`. To start
completely fresh, stop the server, delete `jiak99-dev.db` and run `uv run alembic upgrade head`.

After changing `models.py`:

```
uv run alembic revision --autogenerate -m "add no_show to bookings"   # drafts a migration file
# read the new file and fix it if needed, then:
uv run alembic upgrade head
```

Autogenerate is a draft, not the final word: it can't tell a rename from "drop + add" (which loses data), and a new
required column on a table that already has rows needs a `server_default`. `test_migrations.py` fails if
`models.py` and the migrations ever disagree. Other useful commands: `uv run alembic current` (which version the
database is at), `uv run alembic history`, `uv run alembic downgrade -1` (undo the last migration).

## Docker: api + PostgreSQL

Needs [Docker Desktop](https://www.docker.com/products/docker-desktop/) running. Settings come from `.env`
(optional; copy `.env.example` to `.env` to change ports or the password).

```
docker compose up --build -d     # build the api image, start PostgreSQL and the api in the background
docker compose ps                # both should say "running" (db also "healthy")
docker compose logs -f api       # watch the api log (Ctrl+C stops watching, not the api)
docker compose exec api seed --yes            # load busy-day.json into PostgreSQL
docker compose exec api seed week --yes       # the scenario week
docker compose down              # stop both; the data stays
docker compose down -v           # stop both AND delete the database (fresh start)
```

Then open http://127.0.0.1:8000/docs. On every start the api runs `alembic upgrade head` first, so the tables
always match the code. After changing Python code, run `docker compose up --build -d` again to rebuild.

`seed` needs `--yes` here because PostgreSQL isn't SQLite (the guard against seeding production by accident).

PostgreSQL is also reachable from Windows on `127.0.0.1:5432` (user `jiak`, password `jiak`), e.g. to run
`uv run` commands against it instead of the SQLite file. Write `127.0.0.1`, not `localhost`: Windows tries
IPv6 first for `localhost`, and the connection then hangs forever instead of failing. In PowerShell:

```
$env:DATABASE_URL = "postgresql+psycopg://jiak:jiak@127.0.0.1:5432/jiak"
uv run seed --yes
Remove-Item Env:DATABASE_URL     # back to SQLite in this window
```

### Tests on PostgreSQL

The tests use SQLite by default. The no-double-booking test only proves something on PostgreSQL (SPEC §8),
so run them there too before merging API changes. The database container must be running (`docker compose up -d db`
is enough). In PowerShell:

```
$env:TEST_DATABASE_URL = "postgresql+psycopg://jiak:jiak@127.0.0.1:5432/jiak_test"
uv run pytest apps/api
Remove-Item Env:TEST_DATABASE_URL
```

`jiak_test` is a separate, empty database created when the volume is first made
(`docker/postgres-init/`). The tests wipe it on every test, so never point `TEST_DATABASE_URL` at `jiak`.

## Outlook (Milestone 4, in progress)

The Bookings calendar is the main calendar of the existing `operations@` mailbox (SPEC §5.4). Every booking change
in the app updates its event there: subject like `PHE+CT · Tan family · 18 pax · ABDC · 11:00–14:00`, a fixed body
layout, and the ticket type as category. Cancelling deletes the event. An event the app took over keeps its old text
below a `── Before the app ──` line; the app never changes that part.

**Outlook → app.** When the app starts, and then every 5 minutes (`OUTLOOK_SWEEP_MINUTES`), it reads the calendar
from today to six months ahead:

- an app event **moved** in Outlook, or with a new headcount or ticket in its subject (e.g. `18 pax` → `20 pax`), is
  re-checked like an edit in the app. If it fits, the booking changes. If not, the event is put back and a note at
  the top says why (category "Conflict"). The note goes away with the booking's next change;
- an app event **deleted** in Outlook cancels its booking (but if more than 5 go missing at once, nothing is
  cancelled and the warning banner shows it);
- a **hand-typed** entry like `[PHE] 11:00 · 10A 2C · Tan family` is booked (oldest first) and becomes that
  booking's event, with what was typed kept below the line. No room: a note with the reason and options
  ("Conflict"). Looks like a booking but in another format: a note with the format ("Needs format"). Meetings and
  other events are left alone.

If Outlook can't be reached, bookings still stand; the app keeps trying and catches up when Outlook is back. A
yellow banner on every page says when Outlook hasn't been updated or read for a while, or (with
`AZURE_CLIENT_SECRET_EXPIRES=YYYY-MM-DD` in `.env`) when the app's Outlook password expires within 30 days.
`http://127.0.0.1:8000/api/health` answers 200 when all is well and 503 otherwise, for an uptime checker.

Until the real connection is built, `GRAPH_MODE` stays `fake` (the default): the app writes to an in-memory stand-in
calendar that starts empty at every restart (the app then writes all its events into it afresh), so nothing shows
up in Outlook yet. `GRAPH_MODE=real` stops with "not built yet".

### When the app is down (for staff)

Outlook keeps working when the app doesn't (laptop off or asleep, or a fault), and every booking is in it.

1. **Look** at bookings in the Bookings calendar as usual.
2. **Take a new booking** by adding an event on the right day, with a subject exactly like
   `[PHE] 11:00 · 10A 2C · Tan family`: ticket in brackets (`Tingkat`, `CHE`, `CHE+CT`, `PHE` or `PHE+CT`), start
   time, adults with `A` and children with `C`, the customer. Put contact details, menu and dietary needs in the
   event's text. There is no capacity check meanwhile: **if four groups already overlap that time, don't book it**,
   and tell the customer the details follow.
3. **Change or cancel** a booking on its event: move it to the new date or time, change `18 pax` in the subject, or
   delete the event.

When the app is back it takes all of this in, oldest first. Anything that doesn't fit gets the category "Conflict"
with the reason at the top of the event: sort those out with the customer.

### Taking over the existing calendar (once)

The calendar already holds the bookings typed by hand since 2026-10-08. Save it as an `.ics` file **outside this
repo** (the repo is public; `.gitignore` refuses `*.ics` and `*-review.csv` anyway), then:

```
uv run import-outlook --from-ics E:\MyLocalDev\Jiak_calendaring\raw\jiak-operations.ics --today 2026-11-10
```

This stores nothing. It writes a review sheet next to the `.ics` file (`jiak-operations-review.csv`, opens in
Excel): one row per booking-like event with date, times, people, ticket, client, status and a proposed action
(`book`, `fill in`, `check by hand`, `over 100`, `skip (cancelled)` or `history`), plus notes on what to look at.
Fill in the missing headcounts and ticket types there. An existing sheet is never overwritten (it may hold your
corrections) unless you add `--overwrite`. Clients whose entries never name a ticket type go in
`apps/api/client-tickets.toml`.

Then rehearse the corrected sheet, and apply it once the rehearsal looks right:

```
uv run import-outlook --review E:\MyLocalDev\Jiak_calendaring\raw\jiak-operations-review.csv
uv run import-outlook --review E:\MyLocalDev\Jiak_calendaring\raw\jiak-operations-review.csv --commit
```

Saving the sheet from Excel is fine: dates like `12/11/2026` (day/month/year) and times like `8:30` are read too.
The take-over date is today unless you give `--today`. Rows before it go into a read-only history table
(`past_bookings`). Rows from that date on are booked through the normal enquiry check, as if it were the start of
that day. A booked row's Outlook event is linked to the booking and rewritten in the app's format; no second event
is made. The command lists the rows it didn't book (doesn't fit, over 100 people, missing headcount or ticket, event
no longer in Outlook) and stores nothing for them. Write `skip` in a row's `action` column to leave it out. Running
it again is safe: rows already taken over are skipped. Without `--commit` nothing is stored. It uses `DATABASE_URL`
like the API does. With `GRAPH_MODE` fake, an in-memory copy of the sheet's events stands in for `operations@`.

Test data comes from `uv run anonymise-ics REAL.ics packages/graph/tests/fixtures/operations-anon.ics`, which
replaces client names and keeps only the headcount and product lines of the bodies. Read its output before
committing it. To also test against the real file locally:
`$env:JIAK_REAL_CALENDAR_ICS = "E:\MyLocalDev\Jiak_calendaring\raw\jiak-operations.ics"` before `uv run pytest`.

## The owner's laptop (SPEC §5.5, §5.6)

The app runs on the owner's laptop, with a SQLite database in `%LOCALAPPDATA%\Jiak99` (never in a OneDrive
folder: the app refuses that). **[docs/LAPTOP-INSTALL.md](docs/LAPTOP-INSTALL.md)** is the printable,
step-by-step guide: installing, setting up, everyday use, going live, and **what to do if the laptop is lost,
stolen or broken** (part G, keep a printout with the business papers).

- `scripts\windows\setup-laptop.ps1` saves the settings (`DATABASE_URL`, `JIAK_USER`, `JIAK_BACKUP_DIR`), creates
  the database, turns on auto-start and starts the app; with `-RestoreNewest` it first puts the newest backup
  back (replacement laptop).
- `start-jiak99.ps1` / `stop-jiak99.ps1` start and stop it; `install-autostart.ps1` (`-Remove`) adds the Windows
  task that starts it at sign-in and a desktop shortcut; `update-jiak99.ps1` backs up, updates and restarts.
- **Backups:** every hour while the app runs (and when it closes), into `JIAK_BACKUP_DIR` (normally OneDrive for
  Business). Kept: everything from 2 days, one a day for 30 days. `uv run backup` makes one now.
- **Restore:** stop the app, `uv run restore FILE`. Outlook sync stays paused until the owner has gone through
  http://127.0.0.1:8000/restore-check and pressed **Resume Outlook sync**.
- Every change is recorded under `JIAK_USER` (there is no sign-in on the laptop; the app answers this laptop
  only).

Tests run on GitHub for every push (`.github/workflows/ci.yml`): everything on SQLite, the API tests also on
PostgreSQL 17.

## What the Microsoft 365 admin will need to do

Nothing is needed until the real Graph connection (the last part of Milestone 4). Then (see `SPEC.md` §5.4):

1. **Calendar sharing:** give the staff who need it (the owner first) Editor access to the `operations@` calendar,
   so they see it as a shared calendar in Outlook, e.g.
   `Add-MailboxFolderPermission -Identity operations@<domain>:\Calendar -User <person> -AccessRights Editor`.
2. **App registration** in Microsoft Entra: one app for Jiak99 with the **application** permission
   `Calendars.ReadWrite` (Microsoft Graph), admin-consented. No mail permissions.
3. **Limit the app to `operations@` only** with an Exchange Online application access policy or RBAC for
   Applications, so it can't open any other mailbox. No zone room mailboxes are needed.
4. **Send the developer** the tenant id, the app's client id and a client secret (or certificate). They go into
   `.env` (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `BOOKINGS_MAILBOX`), never into the repo.
5. **Keep the calendar unpublished:** don't publish `operations@`'s calendar as a public ICS link; it shows
   customer phone numbers and emails. (Done 2026-11-10: the link was switched off.)
