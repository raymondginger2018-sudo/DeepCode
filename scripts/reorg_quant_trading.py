#!/usr/bin/env python3
"""
重构 quant_trading/ 目录结构 — 扁平 161 文件 → 按领域分包

用法:
    python scripts/reorg_quant_trading.py          # 执行迁移
    python scripts/reorg_quant_trading.py --dry-run  # 预览不执行
"""
import os
import shutil
import sys
import json
from pathlib import Path

QT_DIR = Path(__file__).resolve().parents[1] / "quant_trading"
BACKUP_DIR = QT_DIR / "_backup_pre_reorg"

# ====== 领域分类映射: 文件名 → 目标子目录 ======
CATEGORY_MAP = {
    # === 策略 ===
    "strategies": [
        "backtest_engine", "dragon_tiger_backtest", "first_bearish", "hedge_engine",
        "historical_backtest", "intraday_strategy", "main_force_backtest", "margin_signals",
        "margin_trading", "margin_vs_index", "oversold_bounce", "pairs_trading",
        "sector_rotation_strategy", "weekly_doji", "lowvol_momentum", "event_driven",
        "limit_up_ladder", "main_force_analyzer", "strategy_template", "strategy_versioning",
        "pit_lift_detector", "live_trading", "rl_execution",
    ],
    # === 分析 ===
    "analysis": [
        "chan_theory", "chanlun_analysis", "chanlun_calibrate", "chanlun_full_pipeline",
        "chan_commercial_aerospace", "chan_electronic_gas", "wyckoff_analyzer", "vsa_analyzer",
        "elliott_wave", "chip_distribution", "cai_nianxian", "yixian_dingqiankun",
        "theme_scanner", "manipulation_detector", "mtp_predictor", "speculative_screener",
        "indicator_suite", "market_state", "multi_timeframe", "sparse_kline",
        "stock_filter", "north_bound", "behavioral_quant", "circuit_breaker",
        "market_temperature", "monte_carlo", "monte_carlo_jax", "sector_heatmap",
        "sector_temperature", "sentiment_analysis", "social_sentiment", "special_data",
        "stats_rigor", "sw_heatmap_plot", "sector_tracker",
        "dragon_tiger", "pit_lift_calibrate",
    ],
    # === 数据 ===
    "data": [
        "akshare_integration", "build_fundamental_cache", "data_cache", "data_engine",
        "data_quality", "data_source_config", "enrich_features", "factor_engine",
        "polars_engine", "realtime_price", "tushare_engine",
        "holiday_calendar", "level2_data", "limit_tracker", "news_aggregator",
        "news_driven", "capital_flow", "sector_data_engine", "adjustment_validator",
        "forward_validator", "lookahead_detector", "persist_tier1", "prewarm_with_alert",
        "macro_engine", "macro_dashboard",
    ],
    # === 机器学习 ===
    "ml": [
        "grpo_optimizer", "grpo_trainer", "knowledge_distill", "knowledge_engine",
        "moe_router", "rag_pipeline", "darts_forecaster", "deepseek_label_all",
        "deploy_distilled", "label_4k", "phase2_annotate", "shap_explainer",
        "glm_data_pipeline", "llm_reasoner_recovered",
        "llm_reasoner", "batch_collect_4k", "batch_scorer", "calibrate_weights",
        "garch_upgrade", "nvidia_vision", "optimizer", "optuna_optimizer",
        "weight_calibrator", "earnings_surprise",
    ],
    # === 基础设施 ===
    "infra": [
        "config", "config_loader", "macro_config", "logger", "structured_logger",
        "notifier", "headroom_bridge", "headroom_helper", "ruflo_bridge",
        "report_utils", "prometheus_metrics", "rest_api", "daily_dashboard",
        "code_index", "task_board", "session_start", "shared", "agent_team",
        "analyst_consensus", "attribution", "bellwether", "bug_fixes",
        "buy_scanner", "chemical_kb", "futures_options", "index_rebalance",
        "institutional_analyzer", "midday_push", "morning_push", "rumor_tracker",
        "seat_tracker", "risk_screener", "risk_manager", "portfolio_engine", "probability_engine", "redis_cache", "signal_stats",
        "upgrade_strategy", "news_push", "migrate_tier1", "migrate_unified_db",
    ],
    # === 报告 ===
    "reports": [
        "build_docs", "daily_auto_update", "daily_review_db", "daily_runner",
        "generate_daily_review", "migrate_daily_review", "news_report",
        "token_usage_report", "fundamental_analyzer",
    ],
    # === 监控/运维 ===
    "ops": [
        "prometheus_metrics",
    ],
}


def find_unmapped(qt_dir):
    """找出未分类的文件"""
    mapped = set()
    for cat, files in CATEGORY_MAP.items():
        for f in files:
            mapped.add(f)

    all_py = set()
    for f in qt_dir.iterdir():
        if f.suffix == ".py" and not f.name.startswith("_"):
            all_py.add(f.stem)

    return sorted(all_py - mapped)


def dry_run(qt_dir):
    """预览迁移结果"""
    print(f"[DRY-RUN] quant_trading/ 结构重组预览")
    print(f"目标目录: {qt_dir}\n")

    for cat, files in sorted(CATEGORY_MAP.items()):
        dest = qt_dir / cat
        print(f"  [{cat}/] ({len(files)} 个文件)")
        for f in sorted(files):
            src = qt_dir / f"{f}.py"
            status = "  [FOUND]" if src.exists() else "  [MISSING!]"
            print(f"    {status} {f}")

    unmapped = find_unmapped(qt_dir)
    if unmapped:
        print(f"\n  [UNMAPPED] {len(unmapped)} 个文件未分类:")
        for f in unmapped:
            print(f"     - {f}")

    print(f"\n总迁移文件: {sum(len(v) for v in CATEGORY_MAP.values())}")
    print(f"未分类文件: {len(unmapped)}")


def do_migrate(qt_dir):
    """执行迁移"""
    # 1. 备份
    if BACKUP_DIR.exists():
        print(f"  删除旧备份...")
        shutil.rmtree(BACKUP_DIR)
    print(f"  备份到 {BACKUP_DIR}...")
    shutil.copytree(qt_dir, BACKUP_DIR, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))

    # 2. 创建子目录
    for cat in CATEGORY_MAP:
        (qt_dir / cat).mkdir(exist_ok=True)
        (qt_dir / cat / "__init__.py").write_text(
            f"# {cat} — 自动生成\n"
        )

    # 3. 移动文件 + 创建桩
    moved_count = 0
    stub_count = 0
    unmapped = find_unmapped(qt_dir)

    for cat, files in CATEGORY_MAP.items():
        for name in files:
            src = qt_dir / f"{name}.py"
            if not src.exists():
                continue
            # 移动到子目录
            dest = qt_dir / cat / f"{name}.py"
            shutil.move(str(src), str(dest))
            moved_count += 1

            # 在原始位置创建桩模块，向后兼容
            # 桩的内容: from quant_trading.{cat}.{name} import *
            stub_content = f"# 自动生成的向后兼容桩 — 文件已迁移至 {cat}/{name}.py\n"
            stub_content += f"from quant_trading.{cat}.{name} import *  # noqa: F401, F403\n"
            src.write_text(stub_content)
            stub_count += 1

    print(f"\n  已迁移: {moved_count} 个文件")
    print(f"  已创建桩: {stub_count} 个")
    print(f"  未分类: {len(unmapped)} 个 (留在原地)")

    # 4. 在主目录写 README
    (qt_dir / "_structure.md").write_text(
        f"""# quant_trading/ 目录结构

重组日期: 自动迁移
原文件数: {moved_count + len(unmapped)} .py
已分类: {moved_count}
未分类: {len(unmapped)}

## 目录说明

| 目录 | 说明 | 文件数 |
|------|------|--------|
"""
        + "".join(f"| {cat} | {cat} | {len(files)} |\n" for cat, files in sorted(CATEGORY_MAP.items(), key=lambda x: -len(x[1])))
        + f"""
## 向后兼容

所有文件在原位置保留了桩模块 (`from quant_trading.{cat}.{name} import *`)，
原有 `from quant_trading.xxx import yyy` 写法无需修改。
"""
    )


def main():
    qt_dir = QT_DIR
    if not qt_dir.exists():
        print(f"[ERROR] 找不到 quant_trading/ 目录: {qt_dir}")
        sys.exit(1)

    if "--dry-run" in sys.argv:
        dry_run(qt_dir)
        return

    print("=" * 60)
    print("  quant_trading/ 结构重组")
    print("=" * 60)
    print(f"\n源目录: {qt_dir}")
    print(f"备份到: {BACKUP_DIR}")

    # 确认
    unmapped = find_unmapped(qt_dir)
    print(f"\n将迁移 {sum(len(v) for v in CATEGORY_MAP.values())} 个文件")
    print(f"未分类 (留在原地): {len(unmapped)} 个")

    if "--yes" not in sys.argv:
        resp = input("\n确认执行? (y/N): ")
        if resp.lower() != "y":
            print("已取消")
            sys.exit(0)

    do_migrate(qt_dir)
    print(f"\n[DONE] 所有向后兼容桩已就绪，现有代码无需修改")


if __name__ == "__main__":
    main()
