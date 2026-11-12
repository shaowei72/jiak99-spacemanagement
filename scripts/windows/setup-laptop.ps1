# Set up the Jiak99 app on the owner's laptop, after Git and uv are installed and the app is
# downloaded to the app folder (docs/LAPTOP-INSTALL.md, parts A and B1-B2). Also used for a
# replacement laptop (SPEC 5.6), with -RestoreNewest.
#
#   powershell -ExecutionPolicy Bypass -File "$env:JIAK_APP_DIR\scripts\windows\setup-laptop.ps1"
#   ... -RestoreNewest    on a replacement laptop: put the newest backup back before the app first starts
#
# It checks that none of the app's files is missing or changed (e.g. removed by antivirus), asks for
# the owner's name and the backup folder, then:
#   - saves DATABASE_URL, JIAK_USER, JIAK_BACKUP_DIR and JIAK_APP_DIR (the app folder, where this
#     script is) as Windows settings for this account;
#   - installs what the app needs and creates the database (if there is none yet);
#   - turns on auto-start and puts a Jiak99 shortcut on the desktop;
#   - starts the app.
# Run it as the owner (not as administrator). Safe to run again: it keeps an existing database.

param(
    [string]$OwnerName = "",
    [string]$BackupFolder = "",
    [switch]$RestoreNewest
)

$ErrorActionPreference = "Stop"
$appFolder = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $appFolder

function Save-Setting([string]$Name, [string]$Value) {
    # A Windows user setting (environment variable): kept for this account, read by new windows
    # and by auto-start. Also set in this window, so the steps below use it.
    [Environment]::SetEnvironmentVariable($Name, $Value, "User")
    Set-Item -Path "Env:$Name" -Value $Value
    Write-Output "    $Name = $Value"
}

function Test-AppFiles {
    # Stops if a file of the app is missing or changed on this laptop: 'git pull' does not bring
    # such a file back. (The same check is in update-jiak99.ps1.)
    $changed = @(git status --porcelain --untracked-files=no)
    if ($LASTEXITCODE -ne 0) { throw "git could not check the app's files (see the message above)." }
    if ($changed.Count -gt 0) {
        Write-Output "Some of the app's files are missing (D) or changed (M) on this laptop:"
        foreach ($line in $changed) { Write-Output "    $line" }
        Write-Output "Put them back with:"
        Write-Output "    cd `"$appFolder`""
        Write-Output "    git restore ."
        Write-Output "then run this script again. Nothing was changed. If a file keeps disappearing,"
        Write-Output "see docs/LAPTOP-INSTALL.md, D3 (antivirus)."
        exit 1
    }
}

Test-AppFiles

# --- the owner's name ---------------------------------------------------------------------------
if (-not $OwnerName) {
    $OwnerName = Read-Host "The owner's name, as it should appear in the change history"
}
if (-not $OwnerName.Trim()) { throw "A name is needed." }

# --- where backups go ---------------------------------------------------------------------------
if (-not $BackupFolder) {
    if ($env:OneDriveCommercial) {
        $suggested = Join-Path $env:OneDriveCommercial "Jiak99 backups"
    } else {
        $suggested = ""
    }
    Write-Output "Backups go to a folder that OneDrive for Business syncs (not a personal OneDrive)."
    if ($suggested) {
        $answer = Read-Host "Backup folder [press Enter for: $suggested]"
        if ($answer.Trim()) { $BackupFolder = $answer.Trim() } else { $BackupFolder = $suggested }
    } else {
        $BackupFolder = Read-Host "Backup folder (OneDrive for Business isn't set up on this account)"
    }
}
if (-not $BackupFolder.Trim()) { throw "A backup folder is needed." }
New-Item -ItemType Directory -Force $BackupFolder | Out-Null

# --- the database, outside OneDrive -------------------------------------------------------------
$dataFolder = Join-Path $env:LOCALAPPDATA "Jiak99"
New-Item -ItemType Directory -Force $dataFolder | Out-Null
$databaseFile = Join-Path $dataFolder "jiak99.db"

Write-Output "Saving the settings:"
Save-Setting "DATABASE_URL" ("sqlite:///" + ($databaseFile -replace '\\', '/'))
Save-Setting "JIAK_USER" $OwnerName.Trim()
Save-Setting "JIAK_BACKUP_DIR" $BackupFolder
Save-Setting "JIAK_APP_DIR" $appFolder

Write-Output "Installing what the app needs (a few minutes the first time):"
uv sync
if ($LASTEXITCODE -ne 0) { throw "uv sync failed: see the messages above." }

if ($RestoreNewest) {
    # A replacement laptop (SPEC 5.6): the newest backup goes back before the app starts, because
    # the app backs up its database at once and an empty one would then be the newest backup.
    $newest = Get-ChildItem $BackupFolder -Filter "jiak99-*.db" | Sort-Object Name | Select-Object -Last 1
    if (-not $newest) {
        throw "No backup in $BackupFolder yet. Wait until OneDrive shows the backups there, then run this again."
    }
    Write-Output "Putting back the newest backup: $($newest.Name)"
    uv run --no-sync restore $newest.FullName
    if ($LASTEXITCODE -ne 0) { throw "The restore failed: see the messages above." }
} else {
    if (Test-Path $databaseFile) {
        Write-Output "Database found: $databaseFile (kept; brought up to date)"
    } else {
        Write-Output "Creating the database: $databaseFile"
    }
    uv run --no-sync alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Creating the database failed: see the messages above." }
}

Write-Output "Turning on auto-start:"
& (Join-Path $PSScriptRoot "install-autostart.ps1")

Write-Output "Starting the app:"
& (Join-Path $PSScriptRoot "start-jiak99.ps1")

Write-Output ""
Write-Output "Done. Open the Jiak99 shortcut on the desktop, or http://127.0.0.1:8000"
Write-Output "Replacement laptop? Stop the app (stop-jiak99.ps1), then: uv run restore <newest backup>"
