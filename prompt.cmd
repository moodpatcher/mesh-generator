@echo off
rem Makes the image-model prompt (asks for the kind and the description) and copies it to the clipboard.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0prompt.ps1" %*
pause
