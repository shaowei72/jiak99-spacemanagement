# Installing Jiak99 on the owner's laptop

A step-by-step guide to print and tick off. It sets the app up on the laptop it will run on (`SPEC.md` §5.5):
one user, the app reachable from this laptop only, data in a small SQLite database file.

**Read this first: what can be done today, and what comes later**

| Part | What | When |
| --- | --- | --- |
| A | Prepare the laptop (encryption, tools) | **Now** |
| B | Install the app and check it works | **Now** |
| C | Try it out with practice data | **Now** |
| D | Start and stop it, keep it up to date, fix common problems | **Now** |
| E | Laptop setup: auto-start, your name in the history, hourly backups, a trial restore | **Now** |
| F | Go-live: the Outlook connection, the calendar take-over, real bookings | **Later**, with Milestone 5 |
| G | If the laptop is lost, stolen or broken | Keep with the business papers |

> **Don't take real bookings in the app yet.** Until part F is done, the app writes to a practice stand-in for
> Outlook, not to the real `operations@` calendar. Keep booking in Outlook as today. Everything you enter now is
> practice data and is wiped at go-live (step F2).

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

### B1. Choose the app folder ☐

Choose where the app goes, e.g. `C:\Jiak99\app`. **Not** in Documents or Desktop: OneDrive often syncs those, and
syncing the app's files while it runs causes trouble. Write the folder on the record sheet.

These commands save it as a Windows setting for this account, `JIAK_APP_DIR`. Every command in this guide then
uses `$env:JIAK_APP_DIR`, so they work whatever folder you chose. Change the first line if you chose another one:

```powershell
$app = "C:\Jiak99\app"
[Environment]::SetEnvironmentVariable("JIAK_APP_DIR", $app, "User")
$env:JIAK_APP_DIR = $app
New-Item -ItemType Directory -Force (Split-Path $app)
```

### B2. Download the app ☐

```powershell
git clone https://github.com/shaowei72/jiak99-spacemanagement $env:JIAK_APP_DIR
cd $env:JIAK_APP_DIR
```

> **Installed before 2026-11-13?** Then `JIAK_APP_DIR` isn't set yet. Set it once, in the folder you installed
> the app in (or run the setup script again, E2, which sets it):
> ```powershell
> cd <your app folder>
> [Environment]::SetEnvironmentVariable("JIAK_APP_DIR", (Get-Location).Path, "User")
> $env:JIAK_APP_DIR = (Get-Location).Path
> ```

### B3. Install what the app needs ☐

```powershell
cd $env:JIAK_APP_DIR
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
cd $env:JIAK_APP_DIR
uv run alembic upgrade head
```

The last lines say `Running upgrade ...`. Check the file exists:

```powershell
Test-Path "$env:LOCALAPPDATA\Jiak99\jiak99.db"
```

It should print `True`.

### B6. Start the app ☐

```powershell
cd $env:JIAK_APP_DIR
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
cd $env:JIAK_APP_DIR
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

- **After part E** the app starts by itself when you sign in to Windows. Open it with the **Jiak99** shortcut on
  the desktop (or the bookmark).
- **By hand** (any time), in PowerShell:
  ```powershell
  powershell -ExecutionPolicy Bypass -File "$env:JIAK_APP_DIR\scripts\windows\start-jiak99.ps1"
  powershell -ExecutionPolicy Bypass -File "$env:JIAK_APP_DIR\scripts\windows\stop-jiak99.ps1"
  ```
  Start does nothing if the app is already running. The app's messages go to
  `%LOCALAPPDATA%\Jiak99\logs` (one file per start, kept 30 days).
- The way in step B6 (`uv run uvicorn ...` in an open window, **Ctrl+C** to stop) still works too.

### D2. Get a new version ☐

When a new version is ready:

```powershell
powershell -ExecutionPolicy Bypass -File "$env:JIAK_APP_DIR\scripts\windows\update-jiak99.ps1"
```

It makes a backup (after part E), stops the app, downloads the new version, installs it, brings the database up
to date (your data stays) and starts the app again. If a step fails it stops there and says which; start the
app again with D1 and pass the message on.

### D3. If something goes wrong

| What you see | What to do |
| --- | --- |
| `uv` or `git` "is not recognized" | Close PowerShell and open a new one. Still there: repeat A5 / A6 |
| The browser says the page can't be reached | The app isn't running: start it (D1). Check the address is `http://127.0.0.1:8000` |
| `address already in use` / port 8000 busy | Another copy is already running. Stop it (D1), then start it again |
| `$env:DATABASE_URL` prints nothing | Repeat B4 (or E1), then **open a new PowerShell window** |
| `cd $env:JIAK_APP_DIR` fails, or `$env:JIAK_APP_DIR` prints nothing | Set it as in the note under B2, then **open a new PowerShell window** |
| Update or setup stops: "Some of the app's files are missing (D) or changed (M)" | Run the `git restore .` it shows, then the script again. `git pull` alone doesn't bring a deleted file back |
| A script file (e.g. `start-jiak99.ps1`) keeps disappearing | Antivirus may be removing it: **Windows Security → Virus & threat protection → Protection history**. If the app's script is listed, choose **Actions → Allow** (or **Restore**), then `git restore .` in the app folder |
| The data looks empty after an update | Check `$env:DATABASE_URL`: the app may be using another database file |
| "... is inside OneDrive" when starting | The database must not be in a OneDrive folder: repeat B4 |
| Yellow bar: "Backup failed" or "No backup since ..." | Check OneDrive for Business is signed in and running (cloud icon by the clock), and that the backup folder exists |
| Yellow bar: "Restored from backup ..." | Open the after-restore list it links to (G5) |
| Another yellow bar | Read it: it says what is wrong with the Outlook link. Note it down and pass it on |
| The app didn't start after signing in | Run start (D1) and read what it says; the logs are in `%LOCALAPPDATA%\Jiak99\logs` |
| Anything else | Copy the red text from PowerShell (select it, right-click) and pass it on |

---

## Part E: Laptop setup: auto-start, your name, backups (now)

Do this once parts A–C are done. Everything is still practice data until part F.

### E1. Check OneDrive for Business ☐

Backups go to the owner's **OneDrive for Business** (the work account), which takes them off the laptop. In
PowerShell:

```powershell
$env:OneDriveCommercial
```

It should print a folder like `C:\Users\<name>\OneDrive - <company>`. If it prints nothing, sign in to OneDrive
with the work account first (OneDrive icon by the clock → **Settings → Account → Add an account**). Don't use a
personal OneDrive: the backups hold customer data.

### E2. Run the setup script ☐

Stop the app if it runs in a window (Ctrl+C), then:

```powershell
cd $env:JIAK_APP_DIR
git pull
powershell -ExecutionPolicy Bypass -File scripts\windows\setup-laptop.ps1
```

It asks two things:

1. **The owner's name**, as it should appear in the change history (e.g. `Owner Name`).
2. **The backup folder.** Press **Enter** to take the one it suggests in OneDrive for Business
   (`...\OneDrive - <company>\Jiak99 backups`).

Then it saves the settings (`DATABASE_URL`, `JIAK_USER`, `JIAK_BACKUP_DIR`), installs, brings the database up to
date (your practice data stays), turns on auto-start, puts a **Jiak99** shortcut on the desktop and starts the app.

### E3. Check auto-start and backups ☐

1. **Sign out of Windows and sign in again.** Within a minute, the **Jiak99** desktop shortcut opens the app.
2. Open **http://127.0.0.1:8000/api/health**: `"lastBackupOk"` shows a time (the first backup is made when the
   app starts).
3. Open the backup folder in File Explorer: a file like `jiak99-2026-11-12-0930.db` is there, with OneDrive's
   green tick (synced).

From now on the app makes a backup every hour while it runs. It keeps every backup from the last 2 days and
one per day for 30 days.

### E4. Try a restore ☐

A backup is only useful if it can be put back. Try it once now, with the practice data:

```powershell
cd $env:JIAK_APP_DIR
uv run backup
powershell -ExecutionPolicy Bypass -File scripts\windows\stop-jiak99.ps1
$newest = Get-ChildItem $env:JIAK_BACKUP_DIR -Filter "jiak99-*.db" | Sort-Object Name | Select-Object -Last 1
uv run restore $newest.FullName
powershell -ExecutionPolicy Bypass -File scripts\windows\start-jiak99.ps1
```

Then open **http://127.0.0.1:8000/restore-check**. It lists what differs between Outlook and the restored data
(during practice: nothing or very little). Press **Resume Outlook sync**. The yellow bar goes away.

The database that was replaced is kept as `jiak99-replaced-<date>-<time>.db` in `%LOCALAPPDATA%\Jiak99`; you can
delete it once all is well. Write the date of this test on the record sheet.

---

## Part F: Go-live with Outlook (later, not yet possible)

These steps need the M365 admin's setup. F3 and F4 have their own sections below the table.

| Step | What | Needs |
| --- | --- | --- |
| F1 | The M365 admin finishes the checklist in `README.md` ("What the Microsoft 365 admin will need to do") and hands over the app's Outlook settings | M365 admin |
| F2 | Wipe the practice data: stop the app (D1); in `%LOCALAPPDATA%\Jiak99` delete `jiak99.db`, `jiak99.db-wal`, `jiak99.db-shm` and any `jiak99-replaced-*.db`; then `cd $env:JIAK_APP_DIR` and `uv run alembic upgrade head` | — |
| F3 | Enter the Outlook settings on the laptop and check the connection, test mailbox first (below) | F1 |
| F4 | Take over the existing calendar (below) | F3, the review sheet filled in |
| F5 | Tell staff about "When the app is down" in `README.md` (typing bookings into Outlook) | — |
| F6 | Start the app (D1) and start taking bookings in it | All of the above |

### F3. The Outlook settings ☐

In PowerShell, with the values from the admin. Type the secret yourself; don't paste it from an email or chat:

```powershell
[Environment]::SetEnvironmentVariable("AZURE_TENANT_ID", "<tenant id>", "User")
[Environment]::SetEnvironmentVariable("AZURE_CLIENT_ID", "<client id>", "User")
[Environment]::SetEnvironmentVariable("AZURE_CLIENT_SECRET", "<secret value>", "User")
[Environment]::SetEnvironmentVariable("AZURE_CLIENT_SECRET_EXPIRES", "<expiry, e.g. 2027-11-30>", "User")
[Environment]::SetEnvironmentVariable("BOOKINGS_MAILBOX", "jiak99-test@<domain>", "User")
[Environment]::SetEnvironmentVariable("GRAPH_MODE", "real", "User")
```

**Open a new PowerShell window**, then check the connection to the test mailbox:

```powershell
cd $env:JIAK_APP_DIR
uv run outlook-check --write-test
```

Every line should start with `OK`. A `PROBLEM` line gives Microsoft's reason: pass it on. Then look at the test
calendar in Outlook: the test event is gone again. When all is well, switch to the real calendar and check it
(read only):

```powershell
[Environment]::SetEnvironmentVariable("BOOKINGS_MAILBOX", "operations@<domain>", "User")
```

Open a new PowerShell window, `cd $env:JIAK_APP_DIR`, `uv run outlook-check`, then restart the app (D1: stop, then
start) so it uses the new settings.

### F4. Take over the existing calendar ☐

```powershell
cd $env:JIAK_APP_DIR
uv run import-outlook --from-graph --out <folder outside the app>\operations-review.csv --carry-over <your corrected sheet>
uv run import-outlook --review <folder outside the app>\operations-review.csv
uv run import-outlook --review <folder outside the app>\operations-review.csv --commit
```

The first line writes a fresh review sheet with your corrections carried over (look at the rows marked `check by
hand`). The second rehearses it (nothing stored); the third applies it. `README.md`, "Taking over the existing
calendar", explains the sheet.

---

## Part G: If the laptop is lost, stolen or broken

Keep a printout of this part with the business papers. Outlook keeps every future booking meanwhile, so the
business carries on: staff book in Outlook as in `README.md` "When the app is down".

### G1. Straight away, if lost or stolen ☐

- **Tell the M365 admin.** They delete the app's Outlook secret at once (Microsoft Entra → **App registrations
  → Jiak99 → Certificates & secrets**), so nobody can use the copy on the lost laptop, and make a new one for
  the replacement. If the owner's Microsoft 365 account was signed in on it, they also sign it out everywhere and
  reset its password.
- **Staff:** book in Outlook meanwhile (`README.md`, "When the app is down").

### G2. The data on the lost laptop ☐

The disk is encrypted (A2), so the data on it can't be read without the recovery key. Keep the key where it is
(A3). The laptop held customer names and phone numbers: check whether Singapore's data protection rules (PDPA)
ask you to report it to the PDPC. Encryption makes harm much less likely, but the decision is yours; this guide
isn't legal advice.

### G3. A replacement laptop ☐

1. Part A (all), then B1, B2 and B3.
2. Part E1. Then wait until the backup folder in OneDrive (the **same** one as before) shows the old backups:
   OneDrive brings them onto the new laptop.
3. Run the setup script with `-RestoreNewest`. It puts the newest backup back **before** the app first starts
   (otherwise the app would back up an empty database first, and that would be the newest):
   ```powershell
   cd $env:JIAK_APP_DIR
   powershell -ExecutionPolicy Bypass -File scripts\windows\setup-laptop.ps1 -RestoreNewest
   ```
   Give the same name and the same backup folder as before.
4. Enter the Outlook settings with the **new** secret from the admin (F3), then restart the app (D1: stop,
   then start).
5. Go through the after-restore list (G5).

### G4. Broken, but still in your hands ☐

No data has left your hands: skip G1's secret and G2. If the laptop will be repaired, take the backups and the
record sheet with you. Then G3 on the repaired or replacement laptop.

### G5. After the restore: the after-restore list ☐

Outlook is newer than the backup (at most about an hour of changes, plus anything typed in Outlook meanwhile).
Outlook sync stays **paused** until you have checked. Open **http://127.0.0.1:8000/restore-check**:

1. **Booked after the backup**: book each one in the app, then delete its old event in Outlook.
2. **Event gone from Outlook**: most likely cancelled after the backup; cancel it in the app (or leave it if it
   is still on).
3. **Changed in Outlook**: edit the booking to match (or leave it).
4. **Typed in Outlook**: for information; the app takes them in after you resume.

Then press **Resume Outlook sync**. Write the date on the record sheet.

---

## Record sheet

Fill in by hand and keep with the business papers (not in the laptop bag).

| | |
| --- | --- |
| Laptop (make, model, name) | |
| Windows account used for the app | |
| Disk encryption on (A2), date | |
| Recovery key kept at (A3) | |
| App folder (B1; also the setting `JIAK_APP_DIR`) | |
| Database file | `%LOCALAPPDATA%\Jiak99\jiak99.db` |
| Installed on (date) | |
| Version installed (`git log -1 --oneline` in the app folder) | |
| M365 admin (name, how to reach) | |
| Trial restore done (E4), date | |
| Go-live date (part F) | |
