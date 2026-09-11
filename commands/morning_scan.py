"""morning_scan — 晨间扫描"""
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORTS_DIR = ROOT / "reports"


def run(args: list[str]) -> None:
    """一键晨间扫描 (超跌+板块+北向)"""
    from quant_trader import print_header, console, C_CYAN, C_GREEN, C_ORANGE

    top_n = int(args[0]) if args else 20
    print_header("MORNING SCAN — 一键晨间扫描")

    console.print(f"  [{C_CYAN}]超跌反弹筛选...[/]")
    from quant_trading.oversold_bounce import OversoldBounceScreener
    df_os = OversoldBounceScreener().screen_all(top_n=top_n)
    if len(df_os) > 0:
        console.print(df_os.to_string(index=False))
        out = REPORTS_DIR / f"morning_scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        df_os.to_json(str(out), orient="records", force_ascii=False, indent=2)
        console.print(f"  [green]超跌结果: {out}[/]")
    else:
        console.print(f"  [{C_ORANGE}]未发现超跌候选[/]")

    console.print(f"\n  [{C_CYAN}]热门板块...[/]")
    try:
        from quant_trading.sector_tracker import hot_sector_scan as _hss
        _hss()
    except Exception:
        pass

    console.print(f"\n  [{C_CYAN}]北向资金...[/]")
    try:
        from quant_trading.north_bound import fetch_north_bound_daily, analyze_north_bound_flow, print_north_bound_report
        nb = fetch_north_bound_daily(5)
        if not nb.empty:
            print_north_bound_report(analyze_north_bound_flow(nb), None)
    except Exception:
        pass

    console.print(f"\n  [green]晨间扫描完成[/]")
