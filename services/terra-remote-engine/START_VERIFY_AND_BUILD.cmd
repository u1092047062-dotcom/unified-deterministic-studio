@echo off
setlocal
cd /d "%~dp0"
echo TerraRemoteEngine 1.2 - Build und Pruefung
echo Dieses Fenster bleibt offen, damit Fehler sichtbar bleiben.
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0VERIFY_AND_BUILD.ps1"
