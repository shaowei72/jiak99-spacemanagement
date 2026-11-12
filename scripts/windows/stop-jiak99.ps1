# Stop the Jiak99 app on this laptop: the program answering on the app's port, and the uv that
# started it. Hourly backups mean at most an hour's changes are only in the database itself when it
# stops this way; the update script makes a backup first.
#
#   powershell -ExecutionPolicy Bypass -File "$env:JIAK_APP_DIR\scripts\windows\stop-jiak99.ps1"

param(
    [int]$Port = 8000
)

$connections = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $connections) {
    Write-Output "Jiak99 is not running."
    exit 0
}

foreach ($connection in $connections) {
    $app = Get-CimInstance Win32_Process -Filter "ProcessId = $($connection.OwningProcess)"
    if ($app) {
        # uvicorn runs inside a python started by uv: stop the parent uv too, if it is one.
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($app.ParentProcessId)" -ErrorAction SilentlyContinue
        Stop-Process -Id $app.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue
        if ($parent -and $parent.Name -eq "uv.exe") {
            Stop-Process -Id $parent.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue
        }
    }
}

# Wait until the port is free.
for ($i = 0; $i -lt 20; $i++) {
    $still = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $still) {
        Write-Output "Jiak99 stopped."
        exit 0
    }
    Start-Sleep -Milliseconds 500
}
Write-Output "Jiak99 is still answering on port $Port. Restart the laptop if this keeps happening."
exit 1
