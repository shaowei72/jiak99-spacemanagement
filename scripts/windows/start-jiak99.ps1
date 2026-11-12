# Start the Jiak99 app on this laptop (SPEC 5.5). Safe to run twice: if the app already answers,
# it does nothing. Auto-start (install-autostart.ps1) runs this script, hidden, when the owner signs in.
#
#   powershell -ExecutionPolicy Bypass -File "$env:JIAK_APP_DIR\scripts\windows\start-jiak99.ps1"
#
# The app's messages go to %LOCALAPPDATA%\Jiak99\logs\app-<date>-<time>.log and .log.err
# (one pair per start, kept 30 days).

param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$appFolder = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$logFolder = Join-Path $env:LOCALAPPDATA "Jiak99\logs"

function Test-AppRunning([int]$Port) {
    # True if something answers on this laptop's app port.
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect("127.0.0.1", $Port)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Find-Uv {
    # uv's usual place, else wherever PATH finds it.
    $usual = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path $usual) { return $usual }
    $found = Get-Command uv -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    throw "uv not found. Install it (docs/LAPTOP-INSTALL.md, step A6)."
}

if (Test-AppRunning $Port) {
    Write-Output "Jiak99 is already running: http://127.0.0.1:$Port"
    exit 0
}

New-Item -ItemType Directory -Force $logFolder | Out-Null
# Old logs go after 30 days.
Get-ChildItem $logFolder -Filter "app-*" -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force -Confirm:$false
$log = Join-Path $logFolder ("app-" + (Get-Date -Format "yyyy-MM-dd-HHmm") + ".log")

$uv = Find-Uv
# --host 127.0.0.1: this laptop only. The app has no sign-in yet (SPEC 5.5).
$arguments = @("run", "--no-sync", "uvicorn", "jiak_api.main:app", "--host", "127.0.0.1", "--port", "$Port")
$process = Start-Process -FilePath $uv -ArgumentList $arguments -WorkingDirectory $appFolder `
    -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError "$log.err" -PassThru

# Wait up to 60 seconds for it to answer.
for ($i = 0; $i -lt 120; $i++) {
    if (Test-AppRunning $Port) {
        Write-Output "Jiak99 started: http://127.0.0.1:$Port (process $($process.Id), log $log)"
        exit 0
    }
    if ($process.HasExited) { break }
    Start-Sleep -Milliseconds 500
}
Write-Output "Jiak99 did not start. See $log and $log.err"
exit 1
