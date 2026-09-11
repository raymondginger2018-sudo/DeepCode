@echo off
chcp 65001 >nul
title Deep Code 自我进化代理 - 安装定时任务
cd /d "%~dp0"

echo ============================================
echo   🧬 Deep Code 自我进化代理 - 定时任务安装
echo ============================================
echo.
echo [*] 创建计划任务：每天 18:05 运行
echo [*] 如果弹出 UAC 窗口，请点击"是"
echo.

schtasks /create /tn "DeepCode SelfEvolve" /tr "F:\DEEPCODE\scripts\run_self_evolve.bat" /sc daily /st 18:05 /rl highest /f

if %errorlevel% equ 0 (
    echo.
    echo [OK] 安装成功！
    echo [OK] 每天 18:05 Deep Code 将自动进行自我进化和诊断。
    echo.
    echo [*] 执行内容：
    echo     ① Git 审查     → DeepSeek 审阅 24h 代码变更
    echo     ② 测试健康     → 跑 pytest 并诊断失败
    echo     ③ 代码扫描     → 扫描关键模块找改进点
    echo     ④ 学习沉淀     → 从 git log 提取经验
    echo     ⑤ 微信推送     → 日报推送到微信
    echo.
    echo [*] 手动测试：双击 run_self_evolve.bat
) else (
    echo.
    echo [FAIL] 安装失败（错误码：%errorlevel%）
    echo [INFO] 请右键本文件，选择"以管理员身份运行"
)

echo.
pause
