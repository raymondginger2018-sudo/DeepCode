"""api — API/数据源状态检查"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(args: list[str]) -> None:
    """数据源与API状态检查 (Tushare/adata)"""
    from quant_trader import print_header, console, C_GREEN, C_ORANGE, C_GRAY
    from quant_trading.tushare_engine import check_connection, print_health, fetch_kline as ts_fetch

    print_header("TUSHARE CONNECTION TEST — 数据源状态")
    print_health()
    if check_connection()["status"] == "ok":
        console.print(f"\n  [{C_GREEN}]✓ Tushare 连接正常[/]")
        df = ts_fetch("000001", 5)
        if df is not None:
            console.print(f"  [{C_GREEN}]✓ 上证指数测试通过 ({len(df)} rows)[/]")
    else:
        console.print(f"\n  [{C_ORANGE}]⚠ Tushare Token 未配置或无效[/]")
        console.print(f"  [{C_GRAY}]→ 注册: https://tushare.pro[/]")
        console.print(f"  [{C_GRAY}]→ 当前自动使用 adata (免费) 备用[/]")
