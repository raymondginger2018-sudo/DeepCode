"""consensus — 分析师一致预期"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORTS_DIR = ROOT / "reports"


def run(args: list[str]) -> None:
    """分析师覆盖 · 盈利预测 · 评级调整 · 目标价"""
    from quant_trader import print_header
    from quant_trading.analyst_consensus import build_consensus_report, print_consensus_report, generate_docx

    print_header("ANALYST CONSENSUS — 一致预期",
                 "分析师覆盖 · 盈利预测 · 评级调整 · 目标价")
    data = build_consensus_report()
    print_consensus_report(data)
    path = generate_docx(data)
    print(f"\n  Word报告: {path}")
