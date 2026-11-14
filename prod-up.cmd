@echo off
rem Start the production stack with a public Cloudflare URL (see scripts\prod-up.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\prod-up.ps1" %*
pause
