param([int]$Port = 8766)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { (Get-Command python).Source }
Push-Location $repoRoot
try {
    & $python -m trading_analysis.server_control stop --port $Port
    $result = $LASTEXITCODE
} finally { Pop-Location }
exit $result
