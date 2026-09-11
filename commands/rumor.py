"""rumor — 小作文追踪"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(args: list[str]) -> None:
    """雪球热帖 · 东财人气榜 · 多平台共振检测"""
    from quant_trader import print_header
    from quant_trading.rumor_tracker import run_rumor_tracker

    print_header("RUMOR TRACKER — 小作文追踪",
                 "雪球热帖 · 东财人气榜 · 多平台共振检测")
    run_rumor_tracker()
