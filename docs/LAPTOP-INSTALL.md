# Installing Jiak99 on the owner's laptop

A step-by-step guide to print and tick off. It sets the app up on the laptop it will run on (`SPEC.md` §5.5):
one user, the app reachable from this laptop only, data in a small SQLite database file.

**Read this first: what can be done today, and what comes later**

| Part | What | When |
| --- | --- | --- |
| A | Prepare the laptop (encryption, tools) | **Now** |
| B | Install the app and check it works | **Now** |
| C | Try it out with practice data | **Now** |
| D | Keep it up to date, start and stop it, fix common problems | **Now** |
| E | Go-live: auto-start, backups, Outlook connection, real bookings | **Later**, once Milestone 4L and part 6 are built |

> **Don't take real bookings in the app yet.** Until part E is done, the app writes to a practice stand-in for
> Outlook, not to the real `operations@` calendar, and nothing is backed up. Keep booking in Outlook as today.
> Everything you enter now is practice data and is wiped at go-live (step E2).

**You need:** the laptop with Windows 10 or 11, an administrator login on it, internet for about 15 minutes,
and this guide. **Time:** about 45 minutes.

**How to type the commands:** open **Windows PowerShell** (press the Windows key, type `PowerShell`, press
Enter). Type or paste each command block exactly, then press Enter. Lines starting with `#` are comments: you can
skip them. Where a step says **as administrator**, right-click Windows PowerShell and choose
**Run as administrator** instead.

---

## Part A: Prepare the laptop

### A1. Sign in as the owner ☐

Sign in to Windows with the **owner's own account** (the one they use every day), not a separate admin
account. The app and its data belong to that account. Administrator rights are only needed for single steps
below, which say so.

### A2. Turn on disk encryption ☐

The database holds customer names and phone numbers. Encryption keeps them unreadable if the laptop is lost
or stolen.

1. Open **Settings → Privacy & security**.
   - If you see **Device encryption**: open it and make sure it is **On**.
   - If you don't see it (Windows Pro or Business): open **Settings → System → Storage → Advanced storage
     settings → Disks & volumes**, or search the Start menu for **Manage BitLocker**, and turn BitLocker **on**
     for drive **C:**.
2. To check, in PowerShell **as administrator**:
   ```powershell
   manage-bde -status C:
   ```
   Look for `Protection Status: Protection On`. (On Windows Home this command may not exist; the Settings page
   showing **On** is enough.)

### A3. Keep the recovery key safe ☐

Without the recovery key, an encrypted disk can't be read after some repairs. Windows normally saves it to the
account you signed in with:

- a **work account** (Microsoft 365): it is under **https://myaccount.microsoft.com → Devices**, or the M365
  admin can find it;
- a **personal Microsoft account**: **https://aka.ms/myrecoverykey**.

Open the right page and check the key for this laptop is there. Note where it is on the record sheet at the
end. Don't keep a printout of the key in the laptop bag.

### A4. Check the time zone ☐

**Settings → Time & language → Date & time**: time zone **(UTC+08:00) Kuala Lumpur, Singapore**, and
**Set time automatically** on. (The app always works in Singapore time; this keeps file times and logs the same.)

### A5. Install Git ☐

Git downloads the app and its updates. In PowerShell **as administrator**:

```powershell
winget install --id Git.Git -e --source winget
```

Close all PowerShell windows. Open a normal (not administrator) PowerShell and check:

```powershell
git --version
```

It should print something like `git version 2.x`.

### A6. Install uv ☐

uv installs the right Python version and everything the app needs. In a normal PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close PowerShell, open it again, and check:

```powershell
uv --version
```

It should print something like `uv 0.12.x`.

---

## Part B: Install the app

### B1. Make the app folder ☐

The app goes in `C:\Jiak99`, **not** in Documents or Desktop: OneDrive often syncs those, and syncing the app's
files while it runs can damage the database.

```powershell
New-Item -ItemType Directory -Force C:\Jiak99
cd C:\Jiak99
```

### B2. Download the app ☐

```powershell
git clone https://github.com/shaowei72/jiak99-spacemanagement app
cd C:\Jiak99\app
git checkout m4-outlook
```

> `git checkout m4-outlook` picks the newest version, which is still on its own branch. Once Milestone 4 is
> merged into `main`, leave that line out (and on a laptop that already has it, run `git checkout main` once).

### B3. Install what the app needs ☐

```powershell
cd C:\Jiak99\app
uv sync
```

This downloads Python 3.13 and the packages (a few minutes the first time). It ends without errors in red.

### B4. Tell the app where its database lives ☐

The database file goes in the owner's local app data folder (`C:\Users\<name>\AppData\Local\Jiak99`), which
OneDrive doesn't sync. These commands create the folder and save the location as a Windows setting for this
account:

```powershell
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\Jiak99"
$db = "$env:LOCALAPPDATA\Jiak99\jiak99.db" -replace '\\', '/'
[Environment]::SetEnvironmentVariable("DATABASE_URL", "sqlite:///$db", "User")
```

**Close PowerShell and open it again** (new windows read the setting), then check:

```powershell
$env:DATABASE_URL
```

It should print `sqlite:///C:/Users/<name>/AppData/Local/Jiak99/jiak99.db`.

### B5. Create the database ☐

```powershell
cd C:\Jiak99\app
uv run alembic upgrade head
```

The last lines say `Running upgrade ...`. Check the file exists:

```powershell
Test-Path "$env:LOCALAPPDATA\Jiak99\jiak99.db"
```

It should print `True`.

### B6. Start the app ☐

```powershell
cd C:\Jiak99\app
uv run uvicorn jiak_api.main:app
```

It prints `Uvicorn running on http://127.0.0.1:8000`. **Leave this window open** while you use the app. If
Windows asks whether to allow access through the firewall, choose **Cancel** / don't allow: the app only needs
this laptop.

### B7. Check it works ☐

1. In the browser open **http://127.0.0.1:8000**. The **Check and book** page appears.
2. Open **http://127.0.0.1:8000/api/health**. It shows `"database":"ok"` and `"ok":true`.
3. Bookmark **http://127.0.0.1:8000** in the browser's bookmarks bar, named **Jiak99**.

### B8. Stop the app ☐

Click in the PowerShell window where it runs and press **Ctrl+C**. The browser page stops answering. (Until
part E adds auto-start, start it with step B6 each time.)

---

## Part C: Try it out with practice data

Everything here is practice and is wiped at go-live.

### C1. Load the sample week (optional) ☐

```powershell
cd C:\Jiak99\app
uv run seed week
```

This adds sample bookings for 1–8 December 2026 (if that week is still ahead). Start the app (B6).

### C2. A short tour ☐

| Page | Try |
| --- | --- |
| **Check and book** | A date, 10:00, PHE, 30 adults → the answer, the reason and other times that fit. Press **Book** |
| **Bookings** | Find it, **Edit** it, then **Cancel booking** |
| **Day plan** | The zones A–D over the day; **Allocate**, pin a route, **Lock day** |
| **Calendar** | The next 6 months; busier days are darker |
| **Settings** | The business settings (turnaround etc.) |

A yellow bar at the top of the page means the app has a problem reaching Outlook. During practice it should not
appear.

---

## Part D: Everyday use and small problems

### D1. Start and stop ☐

- **Start:** PowerShell, then
  ```powershell
  cd C:\Jiak99\app
  uv run uvicorn jiak_api.main:app
  ```
  and open the **Jiak99** bookmark.
- **Stop:** **Ctrl+C** in that PowerShell window, or close the window.

### D2. Get a new version ☐

When a new version is ready: stop the app (D1), then

```powershell
cd C:\Jiak99\app
git pull
uv sync
uv run alembic upgrade head
```

and start it again (D1). `alembic upgrade head` brings the database up to the new version; your data stays.

### D3. If something goes wrong

| What you see | What to do |
| --- | --- |
| `uv` or `git` "is not recognized" | Close PowerShell and open a new one. Still there: repeat A5 / A6 |
| The browser says the page can't be reached | The app isn't running: start it (D1). Check the address is `http://127.0.0.1:8000` |
| `address already in use` / port 8000 busy | Another copy is already running (another PowerShell window). Close it, or use that one |
| `$env:DATABASE_URL` prints nothing | Repeat B4, then **open a new PowerShell window** |
| The data looks empty after an update | Check `$env:DATABASE_URL` (B4): the app may be using another database file |
| A yellow bar at the top of the page | Read it: it says what is wrong with the Outlook link. Note it down and pass it on |
| Anything else | Copy the red text from PowerShell (select it, right-click) and pass it on |

---

## Part E: Go-live (later, not yet possible)

These steps need work that isn't built yet: Milestone 4L (laptop go-live) and Milestone 4 part 6 (the real
Outlook connection, which also needs the M365 admin). This guide will be updated with exact commands then.

| Step | What | Needs |
| --- | --- | --- |
| E1 | The M365 admin finishes the checklist in `README.md` ("What the Microsoft 365 admin will need to do") and hands over the app's Outlook settings | M365 admin |
| E2 | Wipe the practice data: delete `jiak99.db` in `%LOCALAPPDATA%\Jiak99`, then B5 again | — |
| E3 | Enter the Outlook settings on the laptop (`GRAPH_MODE=real` and the admin's values) | Part 6 |
| E4 | Set the owner's name for the change history (`JIAK_USER`) | 4L |
| E5 | Turn on auto-start, so the app starts when the owner signs in to Windows | 4L |
| E6 | Turn on hourly backups into the owner's OneDrive for Business, and try one restore | 4L |
| E7 | Take over the existing calendar: rehearse, then `import-outlook --review ... --commit` | Part 6, the review sheet filled in |
| E8 | Tell staff about "When the app is down" in `README.md` (typing bookings into Outlook) | — |
| E9 | Start taking bookings in the app | All of the above |

---

## Record sheet

Fill in by hand and keep with the business papers (not in the laptop bag).

| | |
| --- | --- |
| Laptop (make, model, name) | |
| Windows account used for the app | |
| Disk encryption on (A2), date | |
| Recovery key kept at (A3) | |
| App folder | `C:\Jiak99\app` |
| Database file | `%LOCALAPPDATA%\Jiak99\jiak99.db` |
| Installed on (date) | |
| Version installed (`git log -1 --oneline` in `C:\Jiak99\app`) | |
| M365 admin (name, how to reach) | |
| Go-live date (part E) | |
