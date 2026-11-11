# Update the Jiak99 app on this laptop to the newest version (docs/LAPTOP-INSTALL.md, D2):
# a backup first, then stop, download, install, bring the database up to date, start.
#
#   powershell -ExecutionPolicy Bypass -File C:\Jiak99\app\scripts\windows\update-jiak99.ps1

param(
    [int]$Port = 8000
)

$appFolder = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $appFolder

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
