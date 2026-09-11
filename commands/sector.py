"""sector — 板块分析"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(args: list[str]) -> None:
    """板块轮动分析 (行业/概念/热力图/温度计)"""
    from quant_trader import sector_analysis, print_header, console

    sub = args[0] if args else ""

    if sub == "temp" or sub == "temperature":
        from quant_trading.sector_temperature import run_sector_temp
        sub2 = args[1] if len(args) > 1 else ""
        if sub2 == "concept":
            print_header("CONCEPT SECTOR THERMOMETER — 概念板块温度计")
            run_sector_temp(mode="concept")
        else:
            print_header("INDUSTRY SECTOR THERMOMETER — 行业板块温度计")
            run_sector_temp(mode="industry")
    elif sub == "heatmap":
        from quant_trading.sector_heatmap import plot_heatmap
        plot_heatmap()
    else:
        print_header("SECTOR ANALYSIS — 板块分析",
                     "板块轮动 · 资金流向 · 强势板块")
        sector_analysis()
