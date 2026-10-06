param([int]$Port = 8766)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pidFile = Join-Path $repoRoot "logs\web_$Port.pid"
$health = $null
try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 3 } catch { }
$serverPid = $null
if ($health.service -eq "trading-analysis" -and $health.project_root -eq $repoRoot) {
    $serverPid = [int]$health.pid
} elseif (Test-Path -LiteralPath $pidFile) {
    $candidate = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
    $listener = @(netstat -ano -p tcp | Select-String ":$Port\s+\S+\s+LISTENING\s+$candidate\s*$")
    if ($listener.Count -gt 0) { $serverPid = $candidate }
}
if (-not $serverPid) {
    Write-Host "No server for this project was identified on port $Port. An older server may need Ctrl+C or manual shutdown."
    exit 0
}
$process = Get-Process -Id $serverPid -ErrorAction SilentlyContinue
if ($process -and $process.ProcessName -notmatch '^python(w)?$') {
    throw "PID $serverPid is not Python; refusing to stop it."
}
if ($process) { Stop-Process -Id $serverPid -Force }
if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile }
Write-Host "Stopped server on port $Port (PID $serverPid)."
