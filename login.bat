@echo off
title Mingle2 Login Helper
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0kill_playwright.ps1" >nul 2>&1
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" login.py
) else (
    python login.py
)
pause
