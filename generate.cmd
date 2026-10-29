@echo off
rem Drop an image on this file (a .ps1 cannot take a dropped file). Options: generate.cmd image.png -Texture 256 -Ps1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0generate.ps1" %*
