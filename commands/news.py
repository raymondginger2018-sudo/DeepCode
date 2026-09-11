"""news — 新闻聚合"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(args: list[str]) -> None:
    """东方财富快讯 · 题材分类 · 时间戳"""
    from quant_trader import print_header
    from quant_trading.news_aggregator import run_news_aggregator

    print_header("NEWS AGGREGATOR — 新闻聚合",
                 "东方财富快讯 · 题材分类 · 时间戳")
    run_news_aggregator()
