"""
scan — 全市场扫描
调用 quant_trader.py 中的 market_scan()
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 延迟导入避免循环
_market_scan = None


def _get_impl():
    global _market_scan
    if _market_scan is None:
        import quant_trader
        _market_scan = quant_trader.market_scan
    return _market_scan


def run(args: list[str]) -> None:
    """全市场扫描 (A股+美股)"""
    _get_impl()()
