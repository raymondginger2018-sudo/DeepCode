"""fundamental — 基本面分析"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(args: list[str]) -> None:
    """基本面分析 (财务指标/估值/行业对比)"""
    from quant_trader import fundamental_analysis, print_header

    code = args[0] if args else None
    print_header("FUNDAMENTAL ANALYSIS — 基本面分析",
                 "财务指标 · 估值 · 行业对比")
    fundamental_analysis(code)
