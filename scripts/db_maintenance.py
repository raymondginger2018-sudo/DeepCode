#!/usr/bin/env python3
"""
DB Maintenance — SQLite 数据库维护工具
═══════════════════════════════════════════
功能:
  1. VACUUM — 回收未使用空间，重组数据库文件
  2. 分析 — 表/索引使用统计
  3. 清理旧数据（可选）— 删除指定天数前的股票日线数据
  4. 报告 — Markdown 格式维护报告

用法:
  python scripts/db_maintenance.py                       # 分析 + VACUUM
  python scripts/db_maintenance.py --vacuum-only          # 只做 VACUUM
  python scripts/db_maintenance.py --analyze-only         # 只做分析
  python scripts/db_maintenance.py --purge-days 365       # 清理由 N 天前的旧数据
  python scripts/db_maintenance.py --dry-run              # 预览模式
  python scripts/db_maintenance.py --schedule daily       # Windows 任务计划注册
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


DB_PATH = Path(__file__).parent.parent / "database.db"
REPORT_DIR = Path(__file__).parent.parent / "auto_fixes" / "reports"


def get_db_stats(cur) -> dict:
    """获取数据库统计信息"""
    stats = {"tables": [], "size_before": 0, "size_after": 0}
    
    # 文件大小
    if DB_PATH.exists():
        stats["size_before"] = os.path.getsize(DB_PATH)
    
    # 表信息
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = cur.fetchall()
    for t in tables:
        name = t[0]
        try:
            cur.execute(f'SELECT COUNT(*) FROM "{name}"')
            count = cur.fetchone()[0]
            stats["tables"].append({"name": name, "rows": count, "size_bytes": 0})
        except Exception:
            stats["tables"].append({"name": name, "rows": -1, "size_bytes": 0})
    
    # 碎片情况
    cur.execute("PRAGMA page_count")
    stats["page_count"] = cur.fetchone()[0]
    cur.execute("PRAGMA freelist_count")
    stats["freelist_count"] = cur.fetchone()[0]
    cur.execute("PRAGMA page_size")
    stats["page_size"] = cur.fetchone()[0]
    
    return stats


def run_vacuum(cur) -> dict:
    """执行 VACUUM 并返回结果"""
    print("[db_maintenance] Running VACUUM...")
    start = time.monotonic()
    cur.execute("VACUUM")
    elapsed = time.monotonic() - start
    
    if DB_PATH.exists():
        size_after = os.path.getsize(DB_PATH)
    else:
        size_after = 0
    
    return {"duration_s": round(elapsed, 1), "size_after": size_after}


def analyze_db(cur) -> dict:
    """运行 ANALYZE 更新索引统计"""
    print("[db_maintenance] Running ANALYZE...")
    start = time.monotonic()
    cur.execute("ANALYZE")
    elapsed = time.monotonic() - start
    return {"duration_s": round(elapsed, 1)}


def purge_old_data(cur, days: int, dry_run: bool = False) -> dict:
    """清理旧数据"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    result = {"cutoff_date": cutoff, "dry_run": dry_run, "deleted": {}}
    
    # stock_daily 表
    try:
        cur.execute("SELECT COUNT(*) FROM stock_daily WHERE trade_date < ?", (cutoff,))
        to_delete = cur.fetchone()[0]
        result["deleted"]["stock_daily"] = to_delete
        if to_delete > 0 and not dry_run:
            cur.execute("DELETE FROM stock_daily WHERE trade_date < ?", (cutoff,))
            print(f"[db_maintenance] Deleted {to_delete} rows from stock_daily (before {cutoff})")
        elif to_delete > 0:
            print(f"[db_maintenance] [DRY-RUN] Would delete {to_delete} rows from stock_daily")
    except Exception as e:
        result["deleted"]["stock_daily"] = f"ERROR: {e}"
    
    return result


def generate_report(stats_before: dict, vacuum_result: dict, analyze_result: dict,
                    purge_result: dict, duration_ms: int) -> str:
    """生成 Markdown 维护报告"""
    size_before_mb = stats_before["size_before"] / 1024 / 1024
    size_after_mb = (vacuum_result.get("size_after", stats_before["size_before"]) or stats_before["size_before"]) / 1024 / 1024
    
    lines = [
        f"# Database 维护报告",
        f"",
        f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**数据库**: `{DB_PATH}`",
        f"**耗时**: {duration_ms}ms",
        f"",
        f"## 摘要",
        f"",
        f"| 指标 | 数值 |",
        f"|:-----|:-----|",
        f"| VACUUM 前 | {size_before_mb:.1f} MB |",
        f"| VACUUM 后 | {size_after_mb:.1f} MB |",
        f"| 回收空间 | {size_before_mb - size_after_mb:.1f} MB |",
        f"| VACUUM 耗时 | {vacuum_result.get('duration_s', 0)}s |",
        f"| Free pages | {stats_before.get('freelist_count', 0)} |",
        f"",
        f"## 表统计",
        f"",
        f"| 表名 | 行数 | 大小 |",
        f"|:-----|:----:|:----:|",
    ]
    
    for t in sorted(stats_before.get("tables", []), key=lambda x: -x["size_bytes"]):
        size_mb = t["size_bytes"] / 1024 / 1024
        lines.append(f"| `{t['name']}` | {t['rows']:,} | {size_mb:.1f} MB |")
    
    if purge_result.get("deleted"):
        lines.extend([
            f"",
            f"## 数据清理",
            f"",
            f"| 表 | 删除行数 |",
            f"|:---|:--------:|",
        ])
        for table, count in purge_result["deleted"].items():
            lines.append(f"| `{table}` | {count:,} |")
    
    return "\n".join(lines)


def setup_schedule(interval: str):
    """注册 Windows 任务计划"""
    script_path = Path(__file__).resolve()
    python = sys.executable
    task_name = "DeepCode DB Maintenance"
    
    if interval == "daily":
        sch_task = [
            "schtasks", "/CREATE", "/TN", task_name, "/TR",
            f'"{python}" "{script_path}"',
            "/SC", "DAILY", "/ST", "03:00", "/F",
        ]
    elif interval == "weekly":
        sch_task = [
            "schtasks", "/CREATE", "/TN", task_name, "/TR",
            f'"{python}" "{script_path}"',
            "/SC", "WEEKLY", "/DAY", "SUN", "/ST", "03:00", "/F",
        ]
    else:
        print(f"Unknown interval: {interval}")
        return False
    
    try:
        subprocess.run(sch_task, capture_output=True, timeout=10)
        print(f"[db_maintenance] 任务计划已注册: {task_name} ({interval})")
        return True
    except Exception as e:
        print(f"[db_maintenance] 注册失败: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="SQLite 数据库维护工具")
    parser.add_argument("--vacuum-only", action="store_true", help="只做 VACUUM")
    parser.add_argument("--analyze-only", action="store_true", help="只做分析")
    parser.add_argument("--purge-days", type=int, default=0, help="清理 N 天前的旧数据")
    parser.add_argument("--dry-run", action="store_true", help="预览模式")
    parser.add_argument("--schedule", choices=["daily", "weekly"], help="注册任务计划")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()
    
    if args.schedule:
        setup_schedule(args.schedule)
        return
    
    start = time.monotonic()
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    
    # 分析：获取维护前统计
    stats_before = get_db_stats(cur)
    print(f"[db_maintenance] 数据库: {stats_before['size_before']/1024/1024:.1f} MB, "
          f"{sum(t['rows'] for t in stats_before['tables'] if t['rows'] > 0):,} 行")
    
    # VACUUM
    vacuum_result = {"duration_s": 0}
    if not args.analyze_only:
        vacuum_result = run_vacuum(cur)
    
    # ANALYZE
    analyze_result = {"duration_s": 0}
    if not args.vacuum_only:
        analyze_result = analyze_db(cur)
    
    # 清理旧数据
    purge_result = {}
    if args.purge_days > 0:
        purge_result = purge_old_data(cur, args.purge_days, args.dry_run)
    
    conn.close()
    
    duration = int((time.monotonic() - start) * 1000)
    
    # 报告
    report = generate_report(stats_before, vacuum_result, analyze_result, purge_result, duration)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"db_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\n[db_maintenance] 报告已保存: {report_path}")
    
    if args.json:
        print(json.dumps({
            "size_before_mb": round(stats_before["size_before"] / 1024 / 1024, 1),
            "vacuum_duration_s": vacuum_result.get("duration_s", 0),
            "freelist_count": stats_before.get("freelist_count", 0),
            "purge_rows": purge_result.get("deleted", {}),
        }, indent=2))


if __name__ == "__main__":
    main()
