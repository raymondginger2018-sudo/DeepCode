@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "F:\DEEPCODE"
echo [*] 小脑 Dreaming 分层增量巩固启动中 (session → experience → knowledge)...
python .deepcode\skills\deepcode-cerebellum\cerebellum_cli.py dreaming --kind session
python .deepcode\skills\deepcode-cerebellum\cerebellum_cli.py dreaming --kind experience
python .deepcode\skills\deepcode-cerebellum\cerebellum_cli.py dreaming --kind knowledge
echo [*] 完成
