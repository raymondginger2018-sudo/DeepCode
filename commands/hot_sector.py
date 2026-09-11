"""hot_sector — 热门板块强势股扫描"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(args: list[str]) -> None:
    """热门板块与强势股扫描"""
    from quant_trader import hot_sector_scan, print_header

    print_header("HOT SECTOR SCAN — 热门板块",
                 "涨跌幅 · 资金流向 · 涨停家数 · 板块轮动")
    hot_sector_scan()
