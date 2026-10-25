@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m app %*
) else (
  python -m app %*
)

endlocal
