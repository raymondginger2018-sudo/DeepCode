"""yixian — 一线定乾坤分析"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(args: list[str]) -> None:
    """一线定乾坤 (牛顿运动定律→价格加速度→多级EMA→乾坤信号线)"""
    from quant_trader import print_header, console
    from quant_trading.yixian_dingqiankun import YixianDingQiankun, _print_report

    code = args[0] if args and args[0].isdigit() and len(args[0]) == 6 else None
    if not code:
        print_header("YIXIAN DINGQIANKUN — 一线定乾坤",
                     "用法: yixian <股票代码>")
        console.print("  [yellow]例: yixian 300657[/]")
        return

    print_header(f"YIXIAN ANALYSIS — 一线定乾坤: {code}",
                 "牛顿运动定律 → 价格加速度 → 多级EMA滤波 → 乾坤信号线")
    yx = YixianDingQiankun()
    report = yx.deep_analyze(code)
    _print_report(report)
    chart_path = yx.generate_chart(code)
    console.print(f"\n  [green]图表已保存: {chart_path}[/]")
