@echo off
rem Start the single-machine demo with the frontend-3d UI (see scripts\demo-up.ps1)
rem Options pass through, e.g. demo-up.cmd -NoTunnel -SkipKnowledgeIndex
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\demo-up.ps1" %*
pause
