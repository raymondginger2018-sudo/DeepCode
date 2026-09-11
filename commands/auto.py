"""auto — 自动质量保障 (Git/Lint/Test)"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(args: list[str]) -> None:
    """自动质量保障: commit (自动提交) / lint (ruff修复) / test (运行测试)"""
    from scripts.auto_quality import main as auto_main
    # 复用 auto_quality.py 的逻辑
    import importlib
    spec = importlib.util.spec_from_file_location("auto_q", str(ROOT / "scripts" / "auto_quality.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.exit(mod.main() if hasattr(mod, 'main') else 0)
