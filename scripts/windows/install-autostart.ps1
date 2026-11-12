# Make the Jiak99 app start by itself when the owner signs in to Windows (SPEC 5.5), and put a
# "Jiak99" shortcut on the desktop. Run it once, as the owner (no administrator needed).
#
#   powershell -ExecutionPolicy Bypass -File "$env:JIAK_APP_DIR\scripts\windows\install-autostart.ps1"
#   ... -Remove        to take auto-start (and the shortcut) away again
#
# It adds a task "Jiak99" to Windows Task Scheduler. The task runs start-jiak99.ps1 hidden at sign-in,
# tries again if it fails, and keeps running on battery.

param(
    [switch]$Remove,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$taskName = "Jiak99"
$startScript = Join-Path $PSScriptRoot "start-jiak99.ps1"
$shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Jiak99.url"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item $shortcut -Force -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Auto-start and the desktop shortcut are removed. The app runs only when started by hand."
    exit 0
}

$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$startScript`" -Port $Port"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Starts the Jiak99 zone capacity app (http://127.0.0.1:$Port)" `
    -Force | Out-Null

# A desktop shortcut that opens the app in the browser.
Set-Content -Path $shortcut -Value "[InternetShortcut]`r`nURL=http://127.0.0.1:$Port/`r`n" -Encoding ASCII

Write-Output "Auto-start is on: the app starts when $user signs in to Windows."
Write-Output "Desktop shortcut: $shortcut"
Write-Output "To start it now without signing out:  Start-ScheduledTask -TaskName $taskName"
