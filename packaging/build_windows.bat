@echo off
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0build_windows.ps1" %*
exit /b %ERRORLEVEL%
