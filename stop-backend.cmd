@echo off
rem Only needed if the backend's window was force-closed and uvicorn survived.
title Stop Leximine backend
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_backend.ps1"
timeout /t 3 >nul
