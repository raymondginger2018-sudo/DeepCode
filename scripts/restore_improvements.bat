@echo off
chcp 65001 >nul
title DeepCode 改进恢复工具

if "%1"=="--ghidra" goto ghidra_only
if "%1"=="-g" goto ghidra_only

echo =====================================================
echo   DeepCode 改进恢复工具
echo   升级后运行此脚本恢复所有改进
echo   用法: restore_improvements.bat [--ghidra]
echo =====================================================
echo.

:: DeepCode (CLONE) — feat/jul26-improvements
echo [1/3] DeepCode (CLONE) ...
cd /d "F:\DEEPCODE"
git checkout feat/jul26-improvements -- core/engine.py 2>nul && echo   [OK] core/engine.py || echo   [--] core/engine.py (跳过)
git checkout feat/jul26-improvements -- parallel_executor.py 2>nul && echo   [OK] parallel_executor.py || echo   [--] parallel_executor.py (跳过)
git checkout feat/jul26-improvements -- cli/session_cli.py 2>nul && echo   [OK] cli/session_cli.py || echo   [--] cli/session_cli.py (跳过)
git checkout feat/jul26-improvements -- cli/tui/commands.py 2>nul && echo   [OK] cli/tui/commands.py || echo   [--] cli/tui/commands.py (跳过)

echo.
echo [2/3] deepcode-engine-mcp ...
cd /d "F:\DEEPCODE\deepcode-engine-mcp"
git checkout gore-toolkit-v1 -- mcp_servers/deepcode_engine_server.py 2>nul && echo   [OK] deepcode_engine_server.py || echo   [--] deepcode_engine_server.py (跳过)

echo.
:ghidra_only
echo [3/3] ghidra-mcp ...
cd /d "F:\DEEPCODE\tools\ghidra-mcp"
git checkout gore-toolkit-v1 -- fun-doc/abi_static.py 2>nul && echo   [OK] abi_static.py || echo   [--] abi_static.py (跳过)
git checkout gore-toolkit-v1 -- python/bridge_mcp_ghidra/config.py 2>nul && echo   [OK] config.py || echo   [--] config.py (跳过)
git checkout gore-toolkit-v1 -- python/bridge_mcp_ghidra/static_tools.py 2>nul && echo   [OK] static_tools.py || echo   [--] static_tools.py (跳过)

echo.
echo =====================================================
echo   恢复完成！
echo   如果某个文件显示 (跳过)，说明分支不存在或文件未改动
echo =====================================================
if "%1"=="" pause
