"""oversold — 超跌反弹全市场扫描"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORTS_DIR = ROOT / "reports"


def run(args: list[str]) -> None:
    """超跌反弹全市场筛选"""
    from quant_trader import print_header, console, C_GREEN, C_ORANGE
    from quant_trading.oversold_bounce import OversoldBounceScreener

    top_n = int(args[0]) if args else 20
    print_header("OVERSOLD BOUNCE SCREEN — 超跌反弹全市场筛选")
    screener = OversoldBounceScreener()
    df = screener.screen_all(top_n=top_n)
    if len(df) > 0:
        console.print(df.to_string(index=False))
        out = REPORTS_DIR / f"oversold_scan_{datetime.now().strftime('%Y%m%d')}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_json(str(out), orient="records", force_ascii=False, indent=2)
        console.print(f"\n  [green]结果已保存: {out}[/]")
    else:
        console.print(f"  [{C_ORANGE}]未发现符合条件的超跌反弹候选[/]")
