@echo off
rem Double-click this when something is not working. It checks every piece --
rem Minecraft, Fabric, the mod, Python, the GPU, the backend, Ollama -- and
rem tells you which one is missing and how to fix it. It changes nothing.
title Leximine setup check
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\doctor.ps1"
