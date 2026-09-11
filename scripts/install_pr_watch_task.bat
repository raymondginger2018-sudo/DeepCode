@echo off
chcp 65001 > nul
title 安装 PR 监控开机自启任务

echo ============================================
echo   安装 PR 监控 - Windows 计划任务
echo ============================================
echo.

:: 获取当前日期用于任务描述
set YEAR=%DATE:~0,4%
set MONTH=%DATE:~5,2%
set DAY=%DATE:~8,2%

:: 删除旧任务（如果存在）
schtasks /delete /tn "DeepCode\PRWatch" /f > nul 2>&1

:: 创建计划任务
schtasks /create ^
  /tn "DeepCode\PRWatch" ^
  /tr "wscript.exe \"F:\DEEPCODE\scripts\start_pr_watch.vbs\"" ^
  /sc ONLOGON ^
  /delay 0000:01:00 ^
  /f ^
  /ru "%USERNAME%" ^
  /it

if %ERRORLEVEL% EQU 0 (
  echo [OK] 计划任务已创建成功！
  echo.
  echo   - 任务名: DeepCode\PRWatch
  echo   - 触发: 用户登录时启动（延迟1分钟）
  echo   - 执行: start_pr_watch.vbs -^> pr_watch.py
  echo.
  echo 下次重启或重新登录后会自动运行。
  echo 你也可以手动测试:
  echo   wscript.exe "F:\DEEPCODE\scripts\start_pr_watch.vbs"
) else (
  echo [ERROR] 创建计划任务失败，请以管理员身份运行。
)

echo.
pause
