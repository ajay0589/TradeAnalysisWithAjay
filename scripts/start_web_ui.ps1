param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8765,
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$python = (Get-Command python).Source
$logsDir = Join-Path $repoRoot "logs"
$outLog = Join-Path $logsDir "web_$Port.out.log"
$errLog = Join-Path $logsDir "web_$Port.err.log"

New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

$listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
foreach ($listener in $listeners) {
    if ($listener.OwningProcess -and $listener.OwningProcess -ne $PID) {
        Stop-Process -Id $listener.OwningProcess -Force
    }
}

if ($Foreground) {
    Set-Location $repoRoot
    & $python -m trading_analysis.web_app --host $HostAddress --port $Port
    exit $LASTEXITCODE
}

Start-Process `
    -FilePath $python `
    -ArgumentList @("-m", "trading_analysis.web_app", "--host", $HostAddress, "--port", "$Port") `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog

Write-Host "Trading analysis UI running at http://$HostAddress`:$Port"
Write-Host "Logs: $outLog"
