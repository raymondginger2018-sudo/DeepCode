"""mcp — MCP 服务管理 (借鉴 Kilo Code MCP Market)"""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(args: list[str]) -> None:
    """MCP 服务管理: list (列出) / status (状态)"""
    from quant_trading.utils.display import console, print_header, C_GREEN, C_RED, C_GRAY, C_CYAN
    import socket

    sub = args[0] if args else "list"

    # 从 settings.json 读取 MCP 配置
    settings_path = ROOT / ".deepcode" / "settings.json"
    if not settings_path.exists():
        console.print("[red]未找到 .deepcode/settings.json[/]")
        return

    with open(settings_path, encoding="utf-8") as f:
        settings = json.load(f)

    servers = settings.get("mcpServers", {})

    if sub == "list":
        print_header(f"MCP 服务列表 ({len(servers)} 个)")
        for name, cfg in sorted(servers.items()):
            cmd = cfg.get("command", "")[:20]
            desc = cfg.get("description", "")
            console.print(f"  [{C_CYAN}]{name:<25s}[/] {cmd:<20s} {desc}")

    elif sub == "status":
        print_header("MCP 服务端口状态")
        # 常见端口健康检查
        port_map = {
            "headroom": 8787,
            "nvidia-proxy": 8765,
            "deepseek-proxy": 8765,
        }
        for name, port in port_map.items():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(("127.0.0.1", port))
            sock.close()
            status = f"[{C_GREEN}]运行中[/]" if result == 0 else f"[{C_RED}]未启动[/]"
            console.print(f"  {name:<25s} 端口 {port:<5d} {status}")

    else:
        console.print(f"[yellow]用法: mcp list|status[/]")
