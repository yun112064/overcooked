@echo off
rem Stop the local dev environment (see scripts\stop-dev.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-dev.ps1" %*
pause
