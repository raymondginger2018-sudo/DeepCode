#!/usr/bin/env python3
"""
deepcode_loop — Claude Code /loop 等效实现
══════════════════════════════════════════════
自动循环执行任务，直到满足目标条件或达到最大轮次。

功能:
  - 定时 tick（默认每 10 分钟）
  - 读取 task_plan.md / progress.md 跟踪进度
  - 运行 check-complete 验证完成状态
  - 满足目标条件后自动停止

用法:
  python scripts/deepcode_loop.py                        # 默认 10m 间隔
  python scripts/deepcode_loop.py --interval 5m          # 5 分钟间隔
  python scripts/deepcode_loop.py --goal "all tests pass" # 自定义目标
  python scripts/deepcode_loop.py --max-rounds 10         # 最多 10 轮
  python scripts/deepcode_loop.py --json                  # JSON 日志输出

集成:
  与 planning-with-files Skill 的 task_plan.md / progress.md 配合使用。
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


def parse_interval(interval: str) -> int:
    """解析时间间隔字符串（如 '10m', '1h', '30s'）为秒数"""
    interval = interval.strip().lower()
    if interval.endswith("s"):
        return int(interval[:-1])
    elif interval.endswith("m"):
        return int(interval[:-1]) * 60
    elif interval.endswith("h"):
        return int(interval[:-1]) * 3600
    else:
        return int(interval) * 60  # 默认分钟


def check_plan_complete(project_root: str) -> tuple[bool, str]:
    """检查 task_plan.md 是否所有阶段完成"""
    plan_path = Path(project_root) / "task_plan.md"
    if not plan_path.exists():
        # 检查 .planning/ 目录
        planning_dir = Path(project_root) / ".planning"
        if planning_dir.exists():
            # 找最新的计划
            plan_dirs = sorted(planning_dir.iterdir()) if planning_dir.is_dir() else []
            if plan_dirs:
                plan_path = plan_dirs[-1] / "task_plan.md"

    if not plan_path.exists():
        return False, "NO_PLAN_FILE"

    content = plan_path.read_text(encoding="utf-8")
    lines = content.split("\n")

    total = 0
    complete = 0
    in_progress = False

    for line in lines:
        line = line.strip()
        # Checkbox format: [ ] [x] [>]
        if "[ ]" in line or "[ ]" in line:
            if "[x]" in line.lower():
                total += 1
                complete += 1
            elif "[>]" in line:
                total += 1
                in_progress = True
            elif "[" in line and "]" in line:
                total += 1
        # Status table format: | ✅ | | ❌ |
        elif "✅" in line or "❌" in line:
            total += 1
            if "✅" in line:
                complete += 1

    if total == 0:
        return False, "NO_TASKS"

    if complete == total:
        return True, f"ALL_COMPLETE ({complete}/{total})"
    elif in_progress:
        return False, f"IN_PROGRESS ({complete}/{total})"
    else:
        return False, f"INCOMPLETE ({complete}/{total})"


def log_progress(project_root: str, message: str, tick: int, goal: str):
    """向 progress.md 追加日志"""
    progress_path = Path(project_root) / "progress.md"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n### Loop Tick #{tick} — {timestamp}\n- **目标**: {goal}\n- **状态**: {message}\n"
    
    if progress_path.exists():
        with open(progress_path, "a", encoding="utf-8") as f:
            f.write(entry)
    else:
        progress_path.write_text(f"# Progress Log\n{entry}", encoding="utf-8")
    
    print(f"[loop] Tick #{tick}: {message}")


def run_check_complete(project_root: str) -> Optional[str]:
    """运行 check-complete 脚本"""
    # 查找 check-complete 脚本
    candidates = [
        Path(project_root) / ".deepcode" / "skills" / "planning-with-files" / "scripts" / "check-complete.sh",
        Path.home() / ".claude" / "skills" / "planning-with-files" / "scripts" / "check-complete.sh",
    ]
    
    for script in candidates:
        if script.exists():
            try:
                cp = subprocess.run(
                    ["bash", str(script)], capture_output=True, text=True, timeout=30,
                    cwd=project_root,
                )
                return cp.stdout.strip() + cp.stderr.strip()
            except Exception as e:
                return f"CHECK_COMPLETE_ERROR: {e}"
    return None


def main():
    parser = argparse.ArgumentParser(
        description="DeepCode Loop — 自动循环执行任务",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--interval", default="10m", help="检查间隔 (默认 10m)")
    parser.add_argument("--goal", default="all phases report Status: complete",
                        help="完成条件 (默认: 所有阶段完成)")
    parser.add_argument("--max-rounds", type=int, default=0,
                        help="最大轮数 (0=无限)")
    parser.add_argument("--project", default=".",
                        help="项目根目录 (默认当前目录)")
    parser.add_argument("--json", action="store_true",
                        help="JSON 格式输出日志")
    parser.add_argument("--once", action="store_true",
                        help="只执行一轮检查，不循环")

    args = parser.parse_args()
    interval_sec = parse_interval(args.interval)
    project_root = Path(args.project).resolve()

    print("=" * 60)
    print("  DeepCode Loop")
    print(f"  项目: {project_root}")
    print(f"  间隔: {args.interval} ({interval_sec}s)")
    print(f"  目标: {args.goal}")
    print(f"  最大轮数: {args.max_rounds or '无限'}")
    print("=" * 60)

    tick = 0
    while True:
        tick += 1
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 检查完成状态
        complete, status = check_plan_complete(str(project_root))
        check_complete_output = run_check_complete(str(project_root))

        # 日志
        message = f"{'✅ ' if complete else '⏳ '}{status}"
        if check_complete_output:
            message += f" | check-complete: {check_complete_output[:100]}"

        log_progress(str(project_root), message, tick, args.goal)

        # JSON 输出
        if args.json:
            print(json.dumps({
                "tick": tick,
                "timestamp": timestamp,
                "complete": complete,
                "status": status,
                "goal": args.goal,
            }))

        if complete:
            print(f"\n✅ 目标达成: {args.goal}")
            print(f"   状态: {status}")
            print(f"   总轮次: {tick}")
            sys.exit(0)

        if args.max_rounds > 0 and tick >= args.max_rounds:
            print(f"\n⏹️  达到最大轮数 ({args.max_rounds})")
            print(f"   状态: {status}")
            sys.exit(1)

        if args.once:
            print(f"\n[loop] 单轮检查完成。状态: {status}")
            sys.exit(0 if complete else 1)

        # 等待下一轮
        print(f"[loop] 等待 {interval_sec}s 后下一轮...\n")
        try:
            time.sleep(interval_sec)
        except KeyboardInterrupt:
            print(f"\n[loop] 用户中断 (已运行 {tick} 轮)")
            sys.exit(1)


if __name__ == "__main__":
    main()
