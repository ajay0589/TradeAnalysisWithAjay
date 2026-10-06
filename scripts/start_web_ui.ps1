param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8766,
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { (Get-Command python).Source }
Push-Location $repoRoot
try {
    if ($Foreground) {
        & $python -m trading_analysis.web_app --host $HostAddress --port $Port
    } else {
        & $python -m trading_analysis.server_control start --host $HostAddress --port $Port
    }
    $result = $LASTEXITCODE
} finally { Pop-Location }
exit $result
