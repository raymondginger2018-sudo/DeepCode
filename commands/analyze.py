"""analyze — 单股技术面分析"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(args: list[str]) -> None:
    """单股技术面分析 (含K线/指标/评分)"""
    from quant_trader import fetch_kline, single_stock_report

    if not args:
        print("用法: analyze <股票代码>\n例: analyze 300438")
        return
    code = args[0]
    df = fetch_kline(code, 250)
    if df.empty:
        print(f"ERROR: 无法获取 {code} 的K线数据")
        sys.exit(1)
    single_stock_report(code, df)
