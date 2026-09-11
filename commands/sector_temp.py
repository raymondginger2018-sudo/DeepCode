"""sector_temp — 板块温度计"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(args: list[str]) -> None:
    """行业/概念板块温度计"""
    from quant_trader import print_header, console
    from quant_trading.sector_temperature import run_sector_temp

    sub = args[0] if args else "industry"
    if sub == "concept":
        print_header("CONCEPT SECTOR THERMOMETER — 概念板块温度计")
        run_sector_temp(mode="concept")
    elif sub and sub not in ("industry", ""):
        print_header(f"SECTOR SEARCH — 板块搜索: {sub}")
        run_sector_temp(mode="industry", search=sub)
    else:
        print_header("INDUSTRY SECTOR THERMOMETER — 行业板块温度计")
        run_sector_temp(mode="industry")
