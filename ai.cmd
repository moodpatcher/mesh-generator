@echo off
rem AI edits through Blender MCP. Drop a finished run's folder (or its .glb) on this file, or: ai.cmd out\name -Agent claude
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ai.ps1" %*
pause
