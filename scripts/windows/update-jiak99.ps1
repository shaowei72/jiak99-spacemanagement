# Update the Jiak99 app on this laptop to the newest version (docs/LAPTOP-INSTALL.md, D2):
# a backup first, then stop, download, install, bring the database up to date, start.
#
#   powershell -ExecutionPolicy Bypass -File "$env:JIAK_APP_DIR\scripts\windows\update-jiak99.ps1"
#
# It first checks that none of the app's files is missing or changed on this laptop (e.g. removed
# by antivirus): 'git pull' does not bring such a file back, so it stops and says how to.

param(
    [int]$Port = 8000
)

$appFolder = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $appFolder

function Test-AppFiles {
    # Stops if a file of the app is missing or changed on this laptop.
    $changed = @(git status --porcelain --untracked-files=no)
    if ($LASTEXITCODE -ne 0) {
        Write-Output "git could not check the app's files (see the message above)."
        exit 1
    }
    if ($changed.Count -gt 0) {
        Write-Output "Some of the app's files are missing (D) or changed (M) on this laptop:"
        foreach ($line in $changed) { Write-Output "    $line" }
        Write-Output "'git pull' does not bring them back. Put them back with:"
        Write-Output "    cd `"$appFolder`""
        Write-Output "    git restore ."
        Write-Output "then run this script again. Nothing was changed. If a file keeps disappearing,"
        Write-Output "see docs/LAPTOP-INSTALL.md, D3 (antivirus)."
        exit 1
    }
}

function Stop-OnError([string]$Step) {
    # Stops the update after a failed step, and says how to get the app back.
    if ($LASTEXITCODE -ne 0) {
        Write-Output ""
        Write-Output "The update stopped at: $Step"
        Write-Output "Nothing after that step was done. Start the app again with start-jiak99.ps1,"
        Write-Output "and pass the messages above on."
        exit 1
    }
}

Write-Output "0/6 Check the app's files"
Test-AppFiles

Write-Output "1/6 Backup"
if ($env:JIAK_BACKUP_DIR) {
    uv run --no-sync backup
    Stop-OnError "the backup"
} else {
    Write-Output "    JIAK_BACKUP_DIR isn't set: no backup made."
}

Write-Output "2/6 Stop the app"
& (Join-Path $PSScriptRoot "stop-jiak99.ps1") -Port $Port

Write-Output "3/6 Download the new version"
git pull
Stop-OnError "git pull"

Write-Output "4/6 Install what it needs"
uv sync
Stop-OnError "uv sync"

Write-Output "5/6 Bring the database up to date"
uv run --no-sync alembic upgrade head
Stop-OnError "alembic upgrade head"

Write-Output "6/6 Start the app"
& (Join-Path $PSScriptRoot "start-jiak99.ps1") -Port $Port
Write-Output ""
Write-Output ("Now at version: " + (git log -1 --oneline))
