# SQLite or PostgreSQL on the laptop?

Status: **proposal, not decided** (2026-11-11). Once decided, the outcome goes into `SPEC.md` and `DECISIONS.md`.

## The situation

- The app will run on the business owner's laptop first ("laptop phase"), and move to a cloud server later when
  the business needs it ("cloud move").
- One person uses the app: the owner, who is also the only one taking bookings.
- The app is only reachable from the laptop itself (`127.0.0.1`), so it has no sign-in yet.
- Outlook (`operations@`) is still where staff see bookings. When the laptop is off, the app is "down" and the
  fall-back in `SPEC.md` §5.4 applies.
- The data is small. Even at several thousand bookings a year the database stays a few megabytes. Speed and
  size don't decide anything here.

So the question is not "which database is better?" but:

1. Which one keeps working every day on a laptop **without anyone looking after it**?
2. Which one is **easy and safe to back up**?
3. Which one makes the **cloud move** cheap?

## Where the app stands today

The code already works with both databases, so either choice is possible without rewriting anything:

- The same tables come from the same Alembic migrations on both. The migrations use `batch_alter_table`, so
  SQLite can do the same changes as PostgreSQL.
- Timestamps are stored in UTC on both (`UTCDateTime` in `models.py` adds back the time zone SQLite can't keep).
- Searches use `lower(...) LIKE`, which behaves the same on both.
- The tests run on SQLite by default, and on PostgreSQL when `TEST_DATABASE_URL` is set.
- `docker compose up` runs the app with PostgreSQL 17. `DEMO.md` runs it with SQLite.

But SQLite is treated as **local development mode** today (`db.py`):

- The per-date booking lock (which stops two bookings taking the last zone at the same moment) is an in-memory
  lock inside the app's process. On PostgreSQL it is a database lock (`pg_advisory_xact_lock`) that works across
  programs.
- No SQLite tuning is switched on (no WAL mode, default 5-second wait when the file is busy).

## The three options

### Option 1: SQLite

The database is a single file on the laptop. The app opens it directly; nothing else runs.

**For**
- **Nothing to keep running.** No database server, no service, no Docker. If the app starts, the database is there.
- **No upkeep.** No passwords, no version upgrades, no ports.
- **Simple, safe backups.** SQLite's built-in backup function makes a consistent copy while the app keeps running.
  The copy is a single file that can go into OneDrive.
- **Simple restore.** Put the backup file back and start the app.
- **Already used** for the demo and most test runs, so it is the best-tested setup day to day.

**Against**
- **Not what the cloud will use.** The cloud move needs a one-off copy of the data into PostgreSQL (see "The cloud
  move" below). About half a working session, plus a rehearsal.
- **Looser checks.** SQLite doesn't enforce text lengths: a 300-character name goes into a 200-character column.
  PostgreSQL would refuse it during the cloud move. Today only the API's customer name has a length limit.
- **One writer at a time.** Fine for one user, but the app writes from several places: the page you're using,
  the Outlook sync after each change, and (after Milestone 4 part 5) the 5-minute Outlook sweep. Without WAL mode
  and a sensible wait setting, one of them can occasionally fail with "database is locked".
- **The booking lock only covers the app itself.** A command like `uv run import-outlook` running while the app is
  open could, in theory, book the same slot at the same moment. The app's own lock doesn't cover the other program.
- **Fragile in synced folders.** If the database file sits in a folder OneDrive syncs (on many business laptops
  that includes Documents and Desktop), syncing it while the app is writing can corrupt it.
- **Some differences show up only on PostgreSQL.** If tests mostly run on SQLite, a PostgreSQL-only problem may
  only surface at the cloud move.

**What it takes to make SQLite safe for real bookings** (about half a working session, part of the laptop setup):

| # | Change | Deals with |
|---|---|---|
| 1 | Keep the live database in a local app folder (e.g. `%LOCALAPPDATA%\Jiak99\`), never in OneDrive, Documents or Desktop | Corruption by sync |
| 2 | Switch on WAL mode and a longer wait when the file is busy | "Database is locked" when the page and the Outlook sync write at once |
| 3 | Start every booking transaction by taking SQLite's write lock (`BEGIN IMMEDIATE`, a documented SQLAlchemy recipe) | Double booking from a second program |
| 4 | Nightly backup with SQLite's backup function into a OneDrive folder, keep 30, try one restore | Losing the laptop or the disk |
| 5 | Forms and API enforce the same text limits as the database columns | Data PostgreSQL would refuse at the cloud move |

### Option 2: PostgreSQL in Docker Desktop

What `docker compose up` already does: PostgreSQL 17 in a container.

**For**
- **The same database as the cloud** (cloud services offer PostgreSQL). Whatever works on the laptop works there.
- **The cloud move is a standard export and import** (`pg_dump` / `pg_restore`). No copy script.
- **Already set up** in `docker-compose.yml`, and the booking lock is the real PostgreSQL one.
- **Strict checks**: wrong lengths and types are refused at once, not at the move.

**Against**
- **Heavy on a laptop.** Docker Desktop runs a Linux virtual machine (WSL2) that typically takes around 2 GB of
  memory while it runs.
- **Has to be running.** If Docker Desktop doesn't start, or the container doesn't come up after a restart or an
  update, the app has no database. On the development PC, Docker Desktop has been off several times, and once a
  stale container answered instead of the new code.
- **Upkeep.** Docker Desktop updates often and sometimes needs attention after Windows updates.
- **Licence terms.** Docker Desktop is free for small businesses (Docker's terms at the time of writing: under
  250 employees and under USD 10 million revenue). Check the current terms before relying on it.
- **Backups** need `pg_dump` run inside or against the container on a schedule.

### Option 3: PostgreSQL installed on Windows

The official PostgreSQL installer for Windows runs it as a Windows service that starts with the laptop.

**For**
- **The same database as the cloud**, with the standard export and import for the move.
- **Lighter than Docker**: no virtual machine; it starts with Windows.
- **Strict checks** and the real booking lock, as in option 2.

**Against**
- **A server to look after.** A database password to keep safe, a service that must stay healthy, and a
  major-version upgrade every few years (each PostgreSQL major version is supported for 5 years).
- **Backups** need `pg_dump` on a schedule (Windows Task Scheduler), with the password available to it.
- **Different from development and tests**, which use SQLite or Docker. A third setup to know.
- **More to explain** if something goes wrong on the laptop and the owner has to fix it alone.

## Side by side

| | SQLite | PostgreSQL in Docker | PostgreSQL on Windows |
|---|---|---|---|
| Runs unattended on a laptop | Best | Weakest | Good |
| Upkeep | None | Most | Some |
| Memory use | Tiny | ~2 GB (VM) | Small |
| Backup | Copy one file (safe copy) | `pg_dump` from the container | `pg_dump` with a password |
| Restore | Put the file back | `pg_restore` | `pg_restore` |
| Same as the cloud | No | Yes | Yes |
| Cloud move | Copy script (~½ session) + rehearsal | Export / import | Export / import |
| Work before real bookings | ~½ session (5 changes above) | Auto-start, backups | Install, auto-start, backups |
| Catches PostgreSQL-only problems early | Only with PostgreSQL tests (below) | Yes | Yes |

## The cloud move, per option

- **From PostgreSQL** (options 2 and 3): `pg_dump` on the laptop, `pg_restore` into the cloud database, point
  `DATABASE_URL` at it.
- **From SQLite** (option 1): a one-off copy script. It reads every table in order (customers before bookings,
  bookings before sessions), writes the rows into PostgreSQL, and resets PostgreSQL's counter for the audit log
  ids. Before copying, it checks every row against PostgreSQL's rules, so nothing fails halfway. Then a rehearsal:
  run the app on the copied data, compare the counts and a few days' plans, and only then switch over. The free
  tool `pgloader` can also copy SQLite into PostgreSQL, but our own script is easier to check and repeat.

Either way, the cloud move also needs things that don't depend on this choice: Microsoft sign-in with the Sales
and Operations roles, hosting with HTTPS, and cloud backups.

## Recommended in any case: tests on PostgreSQL for every change

A GitHub Actions workflow that runs the whole test suite on both SQLite and PostgreSQL on every push (free for
public repositories). This keeps the PostgreSQL code path working during the laptop phase without Docker on
anyone's PC. It fixes the gap we have had several times ("not yet run on PostgreSQL: Docker was off"), and it is
what makes choosing SQLite for the laptop low-risk.

## Recommendation

**SQLite on the laptop**, with the five changes above and the PostgreSQL tests on GitHub.

- For one user on a laptop, "nothing to keep running" matters most. A database that fails to start is an outage,
  and nobody is around to fix it.
- Backup and restore are as simple as they get, which matters most when the laptop is the only copy.
- The extra cost is paid once, at the cloud move: about half a working session for the copy script and a
  rehearsal. The GitHub tests keep that move predictable.

Choose differently if:

- **A second user is likely within a few months.** Then go to the cloud directly rather than spending effort on
  either laptop setup.
- **You want to learn to run PostgreSQL yourself.** Option 3 (installed on Windows) is the reasonable way; avoid
  Docker Desktop on the laptop.

## What doesn't change with the choice

- **BitLocker** on the laptop: the database holds customer names and phone numbers, whichever database it is.
- **Backups off the laptop** into the owner's OneDrive for Business (not a personal cloud: it is customer data),
  nightly, 30 kept, and one restore tried before go-live.
- **The app only listens on `127.0.0.1`**, because it has no sign-in yet.
- **Outlook stays the fall-back** when the laptop is off (`SPEC.md` §5.4 "When the app is down").
