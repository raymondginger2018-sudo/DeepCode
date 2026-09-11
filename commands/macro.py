"""macro — 宏观分析"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(args: list[str]) -> None:
    """宏观经济分析 (GDP/CPI/PMI/社融/M2/北向/两融)"""
    from quant_trader import macro_analysis, print_header

    print_header("MACRO ANALYSIS — 宏观经济分析",
                 "GDP · CPI · PMI · 社融 · M2 · 北向资金 · 两融余额 · 市场估值")
    macro_analysis()
