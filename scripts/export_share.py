#!/usr/bin/env python3
"""
DEEPCODE 分享导出工具
══════════════════════
导出改进后的 DEEPCODE 核心功能，排除:
  1. 量化交易系统 (quant_trading/)
  2. 反编译系统 (tools/ 反编译相关)

用法:
  python scripts/export_share.py /path/to/output_dir
  
  # 可选: 导出后自动初始化 git 仓库
  python scripts/export_share.py /path/to/output_dir --init-git

  # 可选: 导出后自动推送到新仓库
  python scripts/export_share.py /path/to/output_dir --push https://github.com/xxx/deepcode-share.git
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

# ── 要排除的目录/文件 (fnmatch 模式) ──
EXCLUDE_PATTERNS = [
    # 量化交易
    "quant_trading",
    "quant_trader.py",
    "quant_daily_update.ps1",
    # 反编译/逆向工具
    "tools/ghidra-mcp",
    "tools/mcp_decompiler_server.py",
    "tools/deepcode_decompiler.py",
    "tools/IDA*",
    "tools/ghidra*",
    "tools/v8asm*",
    "tools/bun-source",
    "tools/x64dbg*",
    "tools/Accio*",
    # 数据文件
    "*.db",
    "*.db-shm",
    "*.db-wal",
    "*.zip",
    "*.exe",
    "*.vsix",
    "*.bin",
    "*.jsc",
    "node_modules",
    "__pycache__",
    ".git",
    # 个人配置
    ".env",
    ".deepcode/settings.local.json",
    "DEEPCODE_PR_TRACKER*",
    "*.docx",
    "CLAUDE.md",
    "AGENTS.md",
    # 其他项目克隆
    "deepcode-cli-source",
    "DeepCode (CLONE)",
    "deepcode-engine-mcp",
]

# ── 要包含的核心目录/文件 (路径前缀) ──
INCLUDE_PATHS = [
    ".deepcode/",
    "core/",
    "scripts/",
    "auto_fixes/",
    "patches/",
    "mcp_servers_canonical.json",
    ".mcp.json",
    "ecosystem.config.js",
    "README.md",
    "IMPROVEMENTS.md",
    ".gitignore",
]


def should_exclude(rel_path: str) -> bool:
    """检查路径是否在排除列表中"""
    path_str = rel_path.replace("\\", "/")
    for pattern in EXCLUDE_PATTERNS:
        if pattern.endswith("/") and path_str.startswith(pattern):
            return True
        if path_str == pattern or path_str.startswith(pattern + "/"):
            return True
        # 文件名匹配
        if "/" not in pattern and path_str.split("/")[-1] == pattern:
            return True
    return False


def should_include(rel_path: str) -> bool:
    """检查路径是否在包含列表中"""
    path_str = rel_path.replace("\\", "/")
    for prefix in INCLUDE_PATHS:
        if prefix.endswith("/"):
            if path_str.startswith(prefix) or path_str == prefix.rstrip("/"):
                return True
        else:
            if path_str == prefix:
                return True
    return False


def collect_files(src_root: Path) -> list:
    """收集所有需要导出的文件"""
    files = []
    for root, dirs, fnames in os.walk(src_root):
        # 跳过排除目录
        dirs[:] = [d for d in dirs if not should_exclude(
            os.path.relpath(os.path.join(root, d), src_root).replace("\\", "/")
        )]

        for fname in fnames:
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, src_root).replace("\\", "/")

            if should_exclude(rel_path):
                continue
            if should_include(rel_path):
                files.append((full_path, rel_path))

    return files


def export(src_root: Path, dst_root: Path, init_git: bool = False):
    """导出文件到目标目录"""
    files = collect_files(src_root)
    total = len(files)
    total_size = 0

    print(f"即将导出 {total} 个文件到: {dst_root}")
    print()

    dst_root = Path(dst_root)
    dst_root.mkdir(parents=True, exist_ok=True)

    for i, (src_path, rel_path) in enumerate(files, 1):
        dst_path = dst_root / rel_path
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src_path, dst_path)
            size = os.path.getsize(dst_path)
            total_size += size
            if i <= 10 or i % 50 == 0 or i == total:
                print(f"  [{i}/{total}] {rel_path} ({size/1024:.0f}K)")
        except Exception as e:
            print(f"  [{i}/{total}] {rel_path} — 跳过: {e}")

    print()
    print(f"导出完成: {total} 个文件, {total_size/1024/1024:.1f}MB")

    if init_git:
        print()
        print("初始化 git 仓库...")
        subprocess.run(["git", "init"], cwd=dst_root, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=dst_root, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "DeepCode 核心功能分享版"],
            cwd=dst_root, capture_output=True,
        )
        print("Git 仓库已初始化。推送到远程:")
        print(f"  cd {dst_root}")
        print(f"  git remote add origin <你的仓库URL>")
        print(f"  git push -u origin master")

    return dst_root


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    dst = sys.argv[1]
    init_git = "--init-git" in sys.argv

    src = Path(__file__).resolve().parent.parent
    export(src, Path(dst), init_git=init_git)
