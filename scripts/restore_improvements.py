#!/usr/bin/env python3
"""
DeepCode 改进恢复工具 — 升级后一键恢复所有改进

用法:
  python scripts/restore_improvements.py            # 检查 + 恢复
  python scripts/restore_improvements.py --check    # 只检查不恢复
  python scripts/restore_improvements.py --force    # 强制覆盖

功能:
  恢复 3 个仓库的改进:
    1. DeepCode (CLONE) — feat/jul26-improvements 分支
    2. deepcode-engine-mcp — gore-toolkit-v1 分支
    3. ghidra-mcp — gore-toolkit-v1 分支
"""

import os
import sys
import subprocess
import hashlib
from pathlib import Path

# ── 配置 ──────────────────────────────────────────
ROOT = Path(r"F:\DEEPCODE")
VAULT = ROOT / ".deepcode" / "skills" / "deepcode-vault" / "data" / "vault" / "notes"

# 受保护的文件清单
PROTECTED_FILES = {
    # DeepCode (CLONE) — feat/jul26-improvements 分支
    "DeepCode (CLONE)": {
        "repo": ROOT / "DeepCode (CLONE)",
        "branch": "feat/jul26-improvements",
        "files": [
            "core/engine.py",
            "parallel_executor.py",
            "cli/session_cli.py",
            "cli/tui/commands.py",
        ],
    },
    # deepcode-engine-mcp — gore-toolkit-v1 分支
    "deepcode-engine-mcp": {
        "repo": ROOT / "deepcode-engine-mcp",
        "branch": "gore-toolkit-v1",
        "files": [
            "mcp_servers/deepcode_engine_server.py",
        ],
    },
    # ghidra-mcp — gore-toolkit-v1 分支
    "ghidra-mcp": {
        "repo": ROOT / "tools" / "ghidra-mcp",
        "branch": "gore-toolkit-v1",
        "files": [
            "fun-doc/abi_static.py",
        ],
    },
}

# Patch 文件后备
PATCH_BACKUPS = {
    "ghidra-mcp": VAULT / "ghidra_mcp_go_abi_patch.txt",
    "deepcode-engine-mcp": VAULT / "deepcode_engine_v42_patch.txt",
}


def git(*args, cwd: Path) -> str:
    """执行 git 命令"""
    try:
        r = subprocess.run(
            ["git"] + list(args),
            cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        return r.stdout.strip()
    except Exception as e:
        return f"[ERROR] {e}"


def file_hash(path: Path) -> str:
    """计算文件 SHA256"""
    if not path.exists():
        return "MISSING"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def check_improvements() -> dict:
    """检查各仓库改进状态"""
    status = {}
    for name, cfg in PROTECTED_FILES.items():
        repo = cfg["repo"]
        branch = cfg["branch"]
        entries = []

        # 检查分支是否存在
        branches = git("branch", "--list", branch, cwd=repo)
        has_branch = branch in branches

        for rel_path in cfg["files"]:
            full_path = repo / rel_path
            current_hash = file_hash(full_path)

            if has_branch:
                # 获取分支上的版本
                branch_content = git("show", f"{branch}:{rel_path}", cwd=repo)
                if branch_content and not branch_content.startswith("[ERROR]"):
                    branch_hash = hashlib.sha256(branch_content.encode()).hexdigest()[:16]
                else:
                    branch_hash = "NOT_IN_BRANCH"
            else:
                branch_hash = "NO_BRANCH"

            entries.append({
                "file": rel_path,
                "exists": full_path.exists(),
                "current_hash": current_hash,
                "branch_hash": branch_hash,
                "matched": current_hash == branch_hash,
                "branch_exists": has_branch,
            })

        status[name] = entries
    return status


def restore_improvements(force: bool = False) -> int:
    """恢复改进"""
    print("=" * 60)
    print("  DeepCode 改进恢复工具")
    print("=" * 60)

    status = check_improvements()
    restored = 0
    failed = 0
    skipped = 0

    for name, entries in status.items():
        cfg = PROTECTED_FILES[name]
        repo = cfg["repo"]
        branch = cfg["branch"]

        print(f"\n[{name}]")
        print(f"  仓库: {repo}")
        print(f"  分支: {branch}")

        for entry in entries:
            rel = entry["file"]
            if entry["matched"]:
                print(f"  ✅ {rel} — 已保护 (哈希匹配)")
                skipped += 1
                continue

            if not entry["branch_exists"]:
                print(f"  ❌ {rel} — 分支不存在! 尝试 patch 恢复...")
                # 尝试 patch 文件
                patch_file = PATCH_BACKUPS.get(name)
                if patch_file and patch_file.exists():
                    r = git("am", str(patch_file), cwd=repo)
                    if "error" not in r.lower():
                        print(f"     Patch 恢复成功")
                        restored += 1
                    else:
                        print(f"     Patch 失败: {r[:100]}")
                        failed += 1
                else:
                    print(f"     无 patch 备份")
                    failed += 1
                continue

            if not force:
                print(f"  ⚠️  {rel} — 需要恢复 (当前≠分支)")
                print(f"     当前: {entry['current_hash']}")
                print(f"     分支: {entry['branch_hash']}")
                print(f"     使用 --force 恢复")
                skipped += 1
                continue

            # 从分支恢复文件
            r = git("checkout", branch, "--", rel, cwd=repo)
            if "error" not in r.lower():
                print(f"  ✅ {rel} — 已恢复")
                restored += 1
            else:
                print(f"  ❌ {rel} — 恢复失败: {r[:100]}")
                failed += 1

    print(f"\n{'='*60}")
    print(f"  完成: {restored} 恢复, {failed} 失败, {skipped} 跳过")
    print(f"{'='*60}")
    return failed


if __name__ == "__main__":
    check = "--check" in sys.argv
    force = "--force" in sys.argv

    if check:
        status = check_improvements()
        print("=" * 60)
        print("  改进保护状态检查")
        print("=" * 60)
        for name, entries in status.items():
            print(f"\n[{name}]")
            for e in entries:
                status_icon = "🟢" if e["matched"] else ("🟡" if e["exists"] else "🔴")
                print(f"  {status_icon} {e['file']}")
                print(f"     {'存在' if e['exists'] else '缺失':>6} | "
                      f"当前={e['current_hash']} | "
                      f"分支={e['branch_hash']}")
    else:
        sys.exit(restore_improvements(force=force))
