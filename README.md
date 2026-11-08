# Jiak99 zone capacity app

Helps sales and operations sell and allocate 4 identical zones (up to 25 people each). Each group gets a zone of
its own: once a zone is taken, it is sold out for that time slot, however few people are in it.

| Code | Label | Ticket | Length |
| --- | --- | --- | --- |
| `CLASSIC` | CHE | Classic Hawker Experience | 1.5 h |
| `CLASSIC_CT` | CHE+CT | Classic Hawker Experience + Chef's Table | 1.5 h |
| `PREMIUM` | PHE | Premium Hawker Experience | 2 h |
| `PREMIUM_CT` | PHE+CT | Premium Hawker Experience + Chef's Table | 3 h |

Codes are what the database and API store; labels and names are display-only (`engine/rules.py`), so they can be
renamed without touching data.

`SPEC.md` is the contract, `DECISIONS.md` records deviations, `CLAUDE.md` has working rules.

## Status

MVP 0 (Milestone 1, the engine) is done: `packages/engine`, plus a command-line tool to try it.

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

- `--board operations` (default): zones A–D across, time down. Each cell shows the ticket type in that zone
  under the engine's current best plan, with the request added and marked `*`. Only pinned zones (`p`) are
  fixed; the rest may still move until ops allocates the day.
- `--board sales`: start times down, ticket types across. `+` = a group this size can still be sold.

Ticket types can be typed as the label (`PHE`, `che+ct`) or the code, in any case, with `_`, `-` or a space
(`classic-ct`, `Classic CT`).

Write a day file in the same format as `apps/cli/examples/week.json` to try real days.

## Scenario week

`uv run scenarios` checks 29 enquiries against an 8-day test week and compares each answer with the expected
one. How the scenarios were built and what each one tests: `apps/cli/examples/SCENARIOS.md`.

## API and database

The database is `DATABASE_URL` (default: the SQLite file `jiak99-dev.db` in the repo root). Its tables are created
and changed only by Alembic migrations in `apps/api/src/jiak_api/migrations/versions/`.

```
uv run alembic upgrade head                      # create or update the tables (run after every pull)
uv run uvicorn jiak_api.main:app --reload        # dev server; try the routes at http://127.0.0.1:8000/docs
```

Web pages (Milestone 3, in progress): http://127.0.0.1:8000/ opens the **Enquiry check**. A good or costly
verdict and each alternative has a **Book** button: fill in the customer's name (phone, email, company and notes
are optional) and press Book. If the slot filled up after the check (when pressing either Book button), the page
says **No longer fits** and shows fresh alternatives; "Not possible" is only the answer to Check. Each booking has its own page, `/bookings/<id>`.

Read-only views to try in the browser once the server runs (after `uv run seed`):

- http://127.0.0.1:8000/api/availability?date=2026-12-01&n=10 — the sales board for a group of 10
- http://127.0.0.1:8000/api/calendar?from=2026-12-01&to=2027-05-31 — per-day use for the 6-month calendar
- http://127.0.0.1:8000/api/days/2026-12-01 — one day: bookings, zone plan and metrics

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

Each session becomes one booking ("Sample group N"), keeping its zone and pin. Seeding refuses dates that already
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

## What the Microsoft 365 admin will need to do

Nothing yet. Needed from Milestone 4 (see `SPEC.md` §5.4): an Entra app registration with `Calendars.ReadWrite`
application permission, restricted to the bookings and zone mailboxes.
