$ErrorActionPreference = "Stop"

$ports = 7860..7870
$listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in $ports }

if (-not $listeners) {
    Write-Host "No local app is listening on ports 7860-7870."
    exit 0
}

$stopped = 0
foreach ($processId in ($listeners.OwningProcess | Sort-Object -Unique)) {
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
    if ($processInfo.Name -match "^python" -and $processInfo.CommandLine -match "app\.py") {
        Stop-Process -Id $processId -Force
        Write-Host "Stopped old AI Sales Desk process $processId."
        $stopped += 1
    }
    else {
        Write-Host "Port owner $processId was not stopped because it is not a Python app.py process."
    }
}

if ($stopped -eq 0) {
    Write-Host "No matching AI Sales Desk Python process was stopped."
}
