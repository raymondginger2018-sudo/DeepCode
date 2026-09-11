"""dashboard — 启动 Web 仪表盘"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(args: list[str]) -> None:
    """启动 Web 仪表盘 (Dash)"""
    print("Starting Dashboard at http://127.0.0.1:8050 ...")
    from quant_trading.webapp.dashboard import app as _dash_app
    _dash_app.run(debug=False, port=8050)
