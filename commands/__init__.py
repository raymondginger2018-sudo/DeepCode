"""
命令自动发现框架 — quant_trader.py 的 Plugin Command System

用法:
    from commands import get_command, list_commands
    cmd_fn = get_command("scan")
    if cmd_fn:
        cmd_fn(args)

新增命令:
    在 commands/ 下创建 .py 文件，定义一个 run(args) 函数即可自动注册。
    文件名去掉 .py 就是命令名（例如 scan.py → "scan" 命令）。

迁移状态:
    ✅ 已迁移 (12个): scan, flow, oversold, analyze, backtest, macro,
                     sentiment, sector, fundamental, hot_sector,
                     api, dashboard
    ⏳ 待迁移: (其余 ~100 个仍在 quant_trader.py 中)
"""
import importlib
import pkgutil
import sys
from pathlib import Path
from typing import Callable, Optional

_commands: dict[str, Callable] = {}


def _discover():
    """自动发现 commands/ 下的所有命令模块"""
    pkg_dir = Path(__file__).parent
    for importer, modname, ispkg in pkgutil.iter_modules([str(pkg_dir)]):
        if modname.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"commands.{modname}")
            if hasattr(module, "run"):
                _commands[modname] = module.run
        except Exception as e:
            print(f"  [WARN] 命令 {modname} 加载失败: {e}", file=sys.stderr)


def get_command(name: str) -> Optional[Callable]:
    """获取命令函数，不存在返回 None"""
    if not _commands:
        _discover()
    return _commands.get(name)


def list_commands() -> dict[str, str]:
    """列出所有已注册的命令及其描述"""
    if not _commands:
        _discover()
    result = {}
    for name, fn in _commands.items():
        doc = (fn.__doc__ or "").strip().split("\n")[0] if fn.__doc__ else ""
        result[name] = doc
    return result
