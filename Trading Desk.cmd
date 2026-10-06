@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m trading_analysis.desktop_launcher --detach
) else (
    python -m trading_analysis.desktop_launcher --detach
)
if errorlevel 1 (
    echo Unable to open Trading Desk. Check that Python is installed, or open logs\launcher-bootstrap.log.
    pause
)
