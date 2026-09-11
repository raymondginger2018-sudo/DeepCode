"""chanlun — 缠论分析"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORTS_DIR = ROOT / "reports"


def run(args: list[str]) -> None:
    """缠论分析 (分型→笔→线段→中枢→背驰→买卖点)"""
    from quant_trading.chan_theory import ChanTheoryAnalyzer, print_chanlun_report, print_chanlun_screen
    from quant_trader import print_header, console, C_GREEN, C_ORANGE
    import json

    code = args[0] if args and args[0].isdigit() and len(args[0]) == 6 else None

    if code:
        print_header(f"CHANLUN ANALYSIS — 缠论分析: {code}",
                     "分型 → 笔 → 线段 → 中枢 → 背驰 → 买卖点")
        analyzer = ChanTheoryAnalyzer()
        report = analyzer.analyze(code)
        print_chanlun_report(report)
        out = REPORTS_DIR / f"chanlun_{code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        console.print(f"\n  [green]缠论报告已保存: {out}[/]")
    else:
        top_n = int(args[0]) if args else 20
        print_header("CHANLUN SCREEN — 缠论全市场扫描",
                     "分型 → 笔 → 线段 → 中枢 → 背驰 → 买卖点 | 7因子加权评分")
        analyzer = ChanTheoryAnalyzer()
        df = analyzer.screen_all(top_n=top_n)
        print_chanlun_screen(df)
        if len(df) > 0:
            out = REPORTS_DIR / f"chanlun_scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            df.to_json(str(out), orient="records", force_ascii=False, indent=2)
            console.print(f"\n  [green]扫描结果已保存: {out}[/]")
