"""temperature — 市场温度计"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(args: list[str]) -> None:
    """市场温度计 (7维度加权评分)"""
    from quant_trader import print_header, console, C_YELLOW
    from quant_trading.market_temperature import measure_temperature, print_temperature

    print_header("MARKET THERMOMETER — 市场温度计",
                 "7维度加权评分 | 融资+北向+涨跌+量能+涨停+期指+波动")
    result = measure_temperature(verbose=True)
    print_temperature(result)
    alerts = result.get("alerts", [])
    if alerts:
        for a in alerts:
            console.print(f"  [{C_YELLOW}]{a}[/]")
