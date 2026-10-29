@echo off
title Mingle2 One-Click Login & Auto-Sync
cd /d "%~dp0"
echo =========================================================
echo    Mingle2 One-Click Interactive Login ^& Auto-Sync
echo =========================================================
echo Launching visible browser on your PC screen...
python login_and_sync.py
pause
