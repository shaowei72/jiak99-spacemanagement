# Demo on another PC

Runs the whole app (web pages + API) on one Windows PC with a small SQLite database file. No Docker, no
PostgreSQL, no Microsoft 365. Needs internet once for setup (about 5 minutes); after that it works offline.

Type the commands below in **Windows PowerShell**.

## 1. Install uv (once per PC)

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close PowerShell and open it again, so the `uv` command is found. uv installs Python 3.13 by itself in step 3.

## 2. Get the code

Either with Git (if installed):

```powershell
git clone https://github.com/shaowei72/jiak99-spacemanagement
cd jiak99-spacemanagement
```

Or without Git: on https://github.com/shaowei72/jiak99-spacemanagement press the green **Code** button, then
**Download ZIP**. Unzip it, and in PowerShell `cd` into the unzipped folder (the one with this file in it).

## 3. Set up and load the demo data

```powershell
uv sync
uv run alembic upgrade head
uv run seed week
```

`uv sync` downloads Python and the packages. `alembic upgrade head` creates the database file
`jiak99-dev.db`. `seed week` loads the scenario week, 1–8 December 2026 (sample bookings, a closed day and a
blocked time).

## 4. Start the app

```powershell
uv run uvicorn jiak_api.main:app
```

Open **http://127.0.0.1:8000** in the browser. Leave PowerShell open while you demo; press `Ctrl+C` to stop.

## A demo tour (about 10 minutes)

| Page | Try |
| --- | --- |
| **Check and book** | 2026-12-01, 10:00, PHE, 30 adults → verdict, reason, alternatives and every time that fits. Click a time, press **Book**, enter a name. Leave Start empty to see only the times |
| **Calendar** | Find December 2026 (**Later →** if it isn't shown); busier days are darker; click a day |
| **Bookings** | Find the booking you just made; **Edit** it (move it to a full time to see it refused, with alternatives), then **Cancel booking** |
| **Day plan** | 2026-12-01: zones A–D over the day; each group's visits are numbered bars, unsellable slots hatched. Press **Allocate**, pin a route, **Lock day**. 2026-12-04 has every route pinned |
| **Settings** | Turnaround 15 minutes → Save, then look at the Day plan again |
| **API** | http://127.0.0.1:8000/docs lists every route and lets you try it |

The command line works too: `uv run check 2026-12-01 11:30 PREMIUM 10` and `uv run scenarios`.

## Good to know

- **Dates.** Bookings are only possible from today to 6 months ahead. The demo week is in December 2026; on a
  later demo day, check dates from today on instead.
- **Start over.** Stop the app (`Ctrl+C`), delete `jiak99-dev.db`, then run step 3 and 4 again.
- **Get the latest version.** With Git: `git pull`, then `uv sync` and `uv run alembic upgrade head`. With the
  ZIP: download it again into a new folder.
- **This PC only.** The app answers only on this PC (`127.0.0.1`). There is no sign-in yet: everyone acts as
  Operations. Don't open it to the network or the internet until Microsoft sign-in is added.
- **Port 8000 busy?** Start it on another port: `uv run uvicorn jiak_api.main:app --port 8001`, then open
  http://127.0.0.1:8001.
