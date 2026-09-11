@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "F:\DEEPCODE"
echo [*] Deep Code 自我进化代理启动中...
python scripts\self_evolve_agent.py
echo [*] 完成
