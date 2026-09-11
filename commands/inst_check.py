"""inst_check — 机构票分析"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(args: list[str]) -> None:
    """机构持仓分析"""
    from quant_trader import print_header, console, C_RED
    from quant_trading.institutional_analyzer import InstitutionalAnalyzer, print_analysis

    code = args[0] if args else "603979"
    print_header(f"INSTITUTIONAL STOCK ANALYZER — {code}")
    r = InstitutionalAnalyzer().analyze(code)
    if "error" in r:
        console.print(f"  [{C_RED}]{r['error']}[/]")
    else:
        print_analysis(r)
