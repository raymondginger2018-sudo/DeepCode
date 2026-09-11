"""backtest — 策略回测"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(args: list[str]) -> None:
    """策略回测 (单股/批量/首阴/MA双均线)"""
    from quant_trader import fetch_kline, print_header, console, C_CYAN, C_RED
    from quant_trading.backtest_engine import (
        run_backtest,
        print_backtest_results,
        FirstBearishStrategy,
        MACrossStrategy,
    )

    if not args:
        print_header("BATCH BACKTEST — 批量回测")
        console.print(f"\n  [{C_CYAN}]用法: backtest <股票代码> [--first][/]")
        console.print(f"  [{C_CYAN}]例: backtest 300438        — MA双均线回测[/]")
        console.print(f"  [{C_CYAN}]例: backtest 300438 --first — 首阴策略回测[/]")
        return

    code = args[0]
    df = fetch_kline(code, 500)
    if df.empty:
        console.print(f"[{C_RED}]无法获取 {code} 的K线数据[/]")
    else:
        strategy = FirstBearishStrategy if "--first" in args else MACrossStrategy
        r = run_backtest(code, df, strategy)
        if r:
            print_backtest_results([r])
