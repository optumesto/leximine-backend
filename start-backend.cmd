@echo off
rem Double-click target for the Leximine speech backend.
rem A .cmd rather than the .ps1 directly: double-clicking a PowerShell script
rem opens it in an editor, and -ExecutionPolicy Bypass has to come from
rem somewhere anyway.
title Leximine backend
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_backend.ps1"
if errorlevel 1 pause
