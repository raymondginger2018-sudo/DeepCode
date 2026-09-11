@echo off
title DeepCode Daily Review - Task Installer
cd /d "%~dp0"

echo ============================================
echo   DeepCode Daily Review - Task Installer
echo ============================================
echo.
echo [*] Creating scheduled task: daily at 19:00
echo     (runs as current user, no admin needed)
echo.

schtasks /create /tn "DeepCode DailyReview" /tr "F:\DEEPCODE\scripts\run_daily_review.bat" /sc daily /st 19:00 /f

if %errorlevel% equ 0 (
    echo.
    echo [OK] Installed! Runs every day at 19:00:
    echo     1. Generate market review -^> knowledge-vault\notes\
    echo     2. Rebuild RAG index
    echo.
    echo [*] Manual test: scripts\run_daily_review.bat
    echo [*] Uninstall: schtasks /delete /tn "DeepCode DailyReview" /f
) else (
    echo.
    echo [FAIL] Install failed (code: %errorlevel%)
    echo [INFO] If access denied, run as Administrator
)

echo.
pause
