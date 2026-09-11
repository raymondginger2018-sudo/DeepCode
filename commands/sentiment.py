"""sentiment — 市场情绪分析"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(args: list[str]) -> None:
    """市场情绪分析 (新闻/小作文/社交媒体)"""
    from quant_trader import print_header, console

    sub = args[0] if args else ""
    print_header("SENTIMENT ANALYSIS — 市场情绪分析",
                 "新闻聚合 · 小作文追踪 · 社交媒体情绪")

    if sub == "news":
        from quant_trading.news_aggregator import run_news_aggregator
        run_news_aggregator()
    elif sub == "rumor":
        from quant_trading.rumor_tracker import run_rumor_tracker
        run_rumor_tracker()
    else:
        console.print(f"  [yellow]用法: sentiment <subcmd>[/]")
        console.print(f"  [dim]  news   — 新闻聚合[/]")
        console.print(f"  [dim]  rumor  — 小作文追踪[/]")
