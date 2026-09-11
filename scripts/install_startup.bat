@echo off
chcp 65001 >nul
title 安装 PR 监控开机自启

echo 正在添加开机启动项...
set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup

echo Set ws = WScript.CreateObject("WScript.Shell") > "%temp%\create_shortcut.vbs"
echo sc = ws.CreateShortcut("%STARTUP_DIR%\DeepCode PRWatch.lnk") >> "%temp%\create_shortcut.vbs"
echo sc.TargetPath = "wscript.exe" >> "%temp%\create_shortcut.vbs"
echo sc.Arguments = "F:\DEEPCODE\scripts\start_pr_watch.vbs" >> "%temp%\create_shortcut.vbs"
echo sc.WorkingDirectory = "F:\DEEPCODE" >> "%temp%\create_shortcut.vbs"
echo sc.WindowStyle = 7 >> "%temp%\create_shortcut.vbs"
echo sc.Description = "DeepCode PR Merge Monitor" >> "%temp%\create_shortcut.vbs"
echo sc.Save >> "%temp%\create_shortcut.vbs"

cscript /nologo "%temp%\create_shortcut.vbs"
del "%temp%\create_shortcut.vbs"

echo.
echo [OK] 开机启动项已添加！
echo.
echo 下次重启后 PR 监控会自动启动。
echo 你现在也可以手动测试:
echo   wscript.exe "F:\DEEPCODE\scripts\start_pr_watch.vbs"
echo.
pause
