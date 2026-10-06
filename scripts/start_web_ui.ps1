param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8766,
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { (Get-Command python).Source }
$logsDir = Join-Path $repoRoot "logs"
$outLog = Join-Path $logsDir "web_$Port.out.log"
$errLog = Join-Path $logsDir "web_$Port.err.log"
$pidFile = Join-Path $logsDir "web_$Port.pid"

New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

$listeners = @(netstat -ano -p tcp | Select-String ":$Port\s+\S+\s+LISTENING\s+")
if ($listeners.Count -gt 0) {
    throw "Port $Port is already in use. Stop the existing server with .\scripts\stop_web_ui.ps1 -Port $Port before starting again."
}

if ($Foreground) {
    Set-Location $repoRoot
    & $python -m trading_analysis.web_app --host $HostAddress --port $Port
    exit $LASTEXITCODE
}

$serverProcess = Start-Process `
    -FilePath $python `
    -ArgumentList @("-m", "trading_analysis.web_app", "--host", $HostAddress, "--port", "$Port") `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -PassThru

$serverProcess.Id | Set-Content -LiteralPath $pidFile
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    if ($serverProcess.HasExited) {
        throw "Server exited with code $($serverProcess.ExitCode). Read $errLog"
    }
    try {
        $health = Invoke-RestMethod -Uri "http://${HostAddress}:$Port/api/health" -TimeoutSec 2
        $sameProject = $health.project_root -eq $repoRoot.Path -and $health.executable -eq $python
        $newProcess = $health.started_at -and [DateTimeOffset]::Parse($health.started_at).LocalDateTime -ge $serverProcess.StartTime.AddSeconds(-2)
        if ($health.service -eq "trading-analysis" -and $sameProject -and $newProcess) {
            # Windows venv launchers can spawn a child Python process.
            $health.pid | Set-Content -LiteralPath $pidFile
            $ready = $true
            break
        }
    } catch { }
    Start-Sleep -Milliseconds 500
}
if (-not $ready) { throw "Server has not confirmed readiness. Read $errLog" }

Write-Host "Trading analysis UI running at http://$HostAddress`:$Port"
Write-Host "PID: $($health.pid) | Build: $($health.code_version) | Python: $python"
Write-Host "Logs: $outLog"
