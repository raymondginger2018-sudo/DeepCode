"""flow — 资金流向追踪"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(args: list[str]) -> None:
    """资金流向分析 (主力+超大单+北向资金)"""
    from quant_trading.capital_flow import run_capital_flow
    from quant_trader import print_header

    code = args[0] if args else None
    if code:
        print_header(f"CAPITAL FLOW — {code} 资金流向",
                     "多周期验证 · 主力+超大单共振")
    else:
        print_header("CAPITAL FLOW TRACKER — 资金流向追踪",
                     "多周期验证 · 主力+超大单共振 · 区分机构布局 vs 短期炒作")
    run_capital_flow(export=True)
