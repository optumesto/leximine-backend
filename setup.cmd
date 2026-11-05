@echo off
rem Run this once. Puts the backend shortcut on your desktop and installs the
rem mod into Minecraft as its own profile. After this, the desktop shortcut is
rem the only thing you need.
title Leximine setup
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
if errorlevel 1 pause
