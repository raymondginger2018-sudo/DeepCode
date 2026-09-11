#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
① Git 提交信息生成器 — 本地 qwen2.5:3b 生成规范中文提交信息
══════════════════════════════════════════════════════════
用法:
  # 手动使用 (未暂存改动)
  python git_commit_msg.py

  # 暂存区改动
  python git_commit_msg.py --staged

  # 从指定 diff 文件生成
  python git_commit_msg.py --diff-file /path/to/patch.txt

  # 写入 .git/COMMIT_EDITMSG (作为 prepare-commit-msg hook 被 git 调用)
  python git_commit_msg.py --write

  # 安装 git hook (普通提交时自动生成提交信息)
  python git_commit_msg.py --install-hook

  # 卸载 hook
  python git_commit_msg.py --uninstall-hook
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ollama_client import ensure_ollama, generate, LLM_MODEL

MAX_DIFF_CHARS = 4000  # 超出截断，避免本地模型上下文爆炸

SYSTEM_PROMPT = (
    "你是资深 Git 提交信息撰写者。根据代码 diff 生成规范的中文提交信息，"
    "严格遵守以下格式，只输出提交信息本身，不要输出任何解释。"
)


def get_diff(staged: bool = False) -> str:
    """读取 git diff 文本"""
    if staged:
        cmd = ["git", "diff", "--cached"]
    else:
        cmd = ["git", "diff", "HEAD"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        return out.stdout
    except Exception as e:
        print(f"[git_commit_msg] ❌ 读取 diff 失败: {e}", file=sys.stderr)
        return ""


def read_diff_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        print(f"[git_commit_msg] ❌ 文件不存在: {path}", file=sys.stderr)
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def build_prompt(diff: str) -> str:
    files = []
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            f = line.split(" b/")[-1].strip()
            if f:
                files.append(f)
    file_hint = ("\n涉及文件:\n  " + "\n  ".join(files[:30])) if files else ""
    return (
        "根据以下代码变更生成提交信息:\n"
        "要求:\n"
        "1. 首行格式: <type>(<scope>): <subject>，type ∈ {feat, fix, refactor, docs, style, test, chore, perf}\n"
        "2. subject 用中文，≤30 字，说明做了什么\n"
        "3. 若变更较大，追加 2-4 行 body 说明细节，body 每行以 - 开头\n"
        "4. 不要输出 ``` 代码块标记，不要输出多余内容\n"
        f"{file_hint}\n"
        "---- diff 开始 ----\n"
        f"{diff[:MAX_DIFF_CHARS]}"
    )


def generate_message(diff: str) -> str:
    if not diff.strip():
        print("[git_commit_msg] ⚠️ diff 为空，没有可提交的变更。", file=sys.stderr)
        return ""
    msg = generate(build_prompt(diff), model=LLM_MODEL, system=SYSTEM_PROMPT, temperature=0.3)
    # 清理可能的代码块包裹
    msg = msg.strip().strip("`")
    if msg.lower().startswith("```"):
        lines = msg.splitlines()
        msg = "\n".join(lines[1:]).rstrip("`").strip()
    return msg


def write_commit_msg(msg: str, msg_file: str) -> bool:
    """写入 git 提交信息文件 (prepare-commit-msg hook 场景)"""
    try:
        with open(msg_file, "w", encoding="utf-8") as f:
            f.write(msg + "\n")
        return True
    except Exception as e:
        print(f"[git_commit_msg] ❌ 写入 {msg_file} 失败: {e}", file=sys.stderr)
        return False


def install_hook() -> bool:
    """安装 prepare-commit-msg hook (仅普通提交时自动生成，merge/amend 跳过)"""
    try:
        git_dir = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, text=True).stdout.strip()
    except Exception as e:
        print(f"[git_commit_msg] ❌ 无法定位 .git 目录: {e}", file=sys.stderr)
        return False
    hook_dir = Path(git_dir) / "hooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    script = str(Path(__file__).resolve())
    hook_content = (
        "#!/bin/sh\n"
        "# 由 git_commit_msg.py --install-hook 生成: 普通提交时用本地 qwen 生成提交信息\n"
        'MSG_FILE="$1"\n'
        'SOURCE="$2"\n'
        '# 仅普通提交 (无 source) 时生成；merge/amend/squash 保留原信息\n'
        'if [ -z "$SOURCE" ]; then\n'
        f'  python "{script}" --write --msg-file "$MSG_FILE" 2>/dev/null\n'
        "fi\n"
        "exit 0\n"
    )
    hook_path = hook_dir / "prepare-commit-msg"
    hook_path.write_text(hook_content, encoding="utf-8")
    hook_path.chmod(0o755)
    print(f"[git_commit_msg] ✅ hook 已安装: {hook_path}")
    return True


def uninstall_hook() -> bool:
    try:
        git_dir = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, text=True).stdout.strip()
    except Exception as e:
        print(f"[git_commit_msg] ❌ 无法定位 .git 目录: {e}", file=sys.stderr)
        return False
    hook_path = Path(git_dir) / "hooks" / "prepare-commit-msg"
    if hook_path.exists():
        hook_path.unlink()
        print(f"[git_commit_msg] 🗑️ hook 已卸载: {hook_path}")
    else:
        print("[git_commit_msg] ℹ️ 未发现已安装的 hook。")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(prog="git_commit_msg", description="本地 qwen 生成 Git 中文提交信息")
    ap.add_argument("--staged", action="store_true", help="读取暂存区 diff (默认读取 HEAD 未提交改动)")
    ap.add_argument("--diff-file", default="", help="从指定 diff 文件读取")
    ap.add_argument("--write", action="store_true", help="写入 git 提交信息文件")
    ap.add_argument("--msg-file", default="", help="写入目标 (默认 .git/COMMIT_EDITMSG)")
    ap.add_argument("--install-hook", action="store_true", help="安装 prepare-commit-msg hook")
    ap.add_argument("--uninstall-hook", action="store_true", help="卸载 hook")
    args = ap.parse_args()

    if args.install_hook:
        return 0 if install_hook() else 1
    if args.uninstall_hook:
        return 0 if uninstall_hook() else 1

    if not ensure_ollama():
        return 1

    diff = read_diff_file(args.diff_file) if args.diff_file else get_diff(args.staged)
    msg = generate_message(diff)
    if not msg:
        return 1

    print(msg)
    if args.write:
        target = args.msg_file or ".git/COMMIT_EDITMSG"
        return 0 if write_commit_msg(msg, target) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
