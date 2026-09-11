@echo off
chcp 65001 >nul
title Deep Code 小脑 Dreaming - 安装夜间定时任务
echo 本脚本将创建 Windows 计划任务: 每天 02:00 运行小脑分层 Dreaming 增量巩固。
echo 若需修改运行时间, 请编辑本文件中的 /st 参数。
echo.
echo 请右键本文件, 选择"以管理员身份运行"。
pause

schtasks /create /tn "DeepCode Cerebellum Dreaming" /tr "F:\DEEPCODE\scripts\run_dreaming.bat" /sc daily /st 02:00 /rl highest /f

if %errorlevel% == 0 (
    echo.
    echo [OK] 定时任务已创建: 每天 02:00 执行 run_dreaming.bat
) else (
    echo.
    echo [FAIL] 创建失败, 请检查是否以管理员身份运行。
)
pause
