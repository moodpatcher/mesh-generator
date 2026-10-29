@echo off
rem Stops the pipeline and every kep3d Docker container (a generation that lost its window).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
pause
