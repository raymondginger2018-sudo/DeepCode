#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_cerebellum_duals.py — cerebellum skill 双副本单向同步校验
═══════════════════════════════════════════════════════════
背景: deepcode-cerebellum skill 存在双副本:
  .dsh/skills/deepcode-cerebellum/      ← 权威源 (MCP server + scheduler hooks 实际生效,
                                          含 DSH 自足 vendor core/ + Rerank 精排 + JSON Schema 强制输出)
  .deepcode/skills/deepcode-cerebellum/ ← 文档路径 (SKILL.md 声明 + auto_compact/daily_auto_update 等脚本引用)
2026-08-19 事故: .deepcode 副本缺失 4 个 py 文件导致引用方静默失效, 已从 git 恢复。

本脚本: .dsh → .deepcode 单向同步 (默认 dry-run 只报告, --apply 执行)。

排除项 (绝不同步):
  data/        两边各自独立的 SQLite/数据 (17M vs 13M, 同步会互相覆盖)
  __pycache__/ 编译缓存

原则:
  - 只复制 sha256 不同的文件
  - .deepcode 独有文件 (.dsh 无): 只报告不删除 (保守)
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

SRC = Path(r"F:/DEEPCODE/.dsh/skills/deepcode-cerebellum")
DST = Path(r"F:/DEEPCODE/.deepcode/skills/deepcode-cerebellum")
EXCLUDE_DIRS = {"data", "__pycache__"}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def scan() -> tuple[list[Path], list[Path]]:
    """返回 (待同步文件, 目标独有文件)"""
    to_sync, orphan = [], []
    src_files = sorted(
        p for p in SRC.rglob("*")
        if p.is_file() and not any(x in EXCLUDE_DIRS for x in p.relative_to(SRC).parts)
    )
    for sf in src_files:
        rel = sf.relative_to(SRC)
        df = DST / rel
        if not df.exists() or sha256(sf) != sha256(df):
            to_sync.append(sf)
    # 目标独有 (保守, 只报告)
    dst_files = sorted(
        p for p in DST.rglob("*")
        if p.is_file() and not any(x in EXCLUDE_DIRS for x in p.relative_to(DST).parts)
    )
    src_set = {p.relative_to(SRC) for p in src_files}
    orphan = [p for p in dst_files if p.relative_to(DST) not in src_set]
    return to_sync, orphan


def main() -> int:
    ap = argparse.ArgumentParser(description="cerebellum skill 双副本单向同步 (.dsh → .deepcode)")
    ap.add_argument("--apply", action="store_true", help="实际同步 (默认 dry-run 只报告)")
    args = ap.parse_args()

    to_sync, orphan = scan()

    print(f"源目录 : {SRC}")
    print(f"目标目录: {DST}")
    print(f"排除   : {', '.join(sorted(EXCLUDE_DIRS))}")
    print(f"\n待同步文件: {len(to_sync)} 个")
    for sf in to_sync:
        rel = sf.relative_to(SRC)
        df = DST / rel
        mark = "新增" if not df.exists() else "更新"
        print(f"  [{mark}] {rel}")

    if orphan:
        print(f"\n⚠️ 目标独有文件 (不删除, 仅报告): {len(orphan)} 个")
        for p in orphan:
            print(f"  [独有] {p.relative_to(DST)}")

    if not to_sync:
        print("\n✅ 两副本已一致, 无需同步")
        return 0

    if not args.apply:
        print("\n(dry-run: 使用 --apply 执行同步)")
        return 0

    print("\n开始同步 ...")
    n = 0
    for sf in to_sync:
        rel = sf.relative_to(SRC)
        df = DST / rel
        df.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sf, df)
        n += 1
        print(f"  ✅ {rel}")
    print(f"\n✅ 同步完成: {n} 个文件")

    # 复检
    left, _ = scan()
    if left:
        print(f"⚠️ 复检仍有 {len(left)} 个差异:")
        for sf in left:
            print(f"  - {sf.relative_to(SRC)}")
        return 1
    print("✅ 复检通过: 两副本完全一致 (排除 data/__pycache__)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
