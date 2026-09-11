#!/usr/bin/env python3
"""
PR 状态监控 — 定期检查 PR 是否被 merge，并在桌面上弹通知。

用法:
  python scripts/pr_watch.py              # 一次性检查
  python scripts/pr_watch.py --watch      # 持续监控（每 30 分钟检查一次）
  python scripts/pr_watch.py --watch --interval 15  # 每 15 分钟检查一次
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# ── 在这里配置你要监控的 PR ────────────────────────────────────
WATCH_LIST = [
    {
        "repo": "HKUDS/DeepCode",
        "pr": 140,
        "title": "DeepCode 主项目 — MoE/模型层级/expert路由",
    },
    {
        "repo": "lessweb/deepcode-cli",
        "pr": 258,
        "title": "DeepCode CLI — Permission Profile / SQLite 日志 / Job 队列 / Skill",
    },
    {
        "repo": "lessweb/deepcode-cli",
        "pr": 263,
        "title": "DeepCode CLI — 遥测指标/Strict MCP",
    },
    {
        "repo": "lessweb/deepcode-cli",
        "pr": 266,
        "title": "DeepCode CLI — Agentic core / snapshot-restore / skill",
    },
]
# ───────────────────────────────────────────────────────────────

TOKEN = os.environ.get("GITHUB_TOKEN", "")

STATE_FILE = Path(__file__).resolve().parent.parent / ".pr_watch_state.json"


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def check_pr(repo: str, pr_number: int) -> dict | None:
    """通过 GitHub API 查询 PR 状态，返回 PR 信息或 None（失败时）。"""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "pr-watch-script",
    }
    if TOKEN:
        headers["Authorization"] = f"token {TOKEN}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [ERROR] 请求失败: {e}")
        return None


def notify_win(title: str, message: str):
    """Windows 桌面弹窗 (PowerShell MessageBox)"""
    encoded_title = title.replace("'", "''")
    encoded_msg = message.replace("'", "''")
    ps_script = (
        f'Add-Type -AssemblyName System.Windows.Forms; '
        f'[System.Windows.Forms.MessageBox]::Show(\'{encoded_msg}\', \'{encoded_title}\')'
    )
    try:
        subprocess.run(
            ["powershell", "-Command", ps_script],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass  # 弹窗失败不阻塞


def notify_all(title: str, message: str):
    """跨平台通知：Windows 弹窗 + 控制台横幅"""
    border = "=" * len(title)
    print(f"\n{border}")
    print(title)
    print(border)
    print(f"{message}\n")
    if sys.platform == "win32":
        notify_win(title, message)


def run_once(verbose: bool = True) -> list[dict]:
    """检查所有 PR，返回有状态变化的 PR 列表。"""
    state = load_state()
    changes = []

    for item in WATCH_LIST:
        repo = item["repo"]
        pr_num = item["pr"]
        label = item["title"]

        if verbose:
            print(f"  [..] {repo} #{pr_num} ({label})...", end=" ", flush=True)

        data = check_pr(repo, pr_num)
        if data is None:
            if verbose:
                print("[FAIL]")
            continue

        current_state = data.get("state", "unknown")  # open / closed
        merged = data.get("merged", False)
        merged_at = data.get("merged_at", None)

        # 判断最终状态
        if merged and merged_at:
            status = f"[MERGED] at {merged_at}"
        elif current_state == "closed" and not merged:
            status = "[CLOSED] (without merge)"
        elif current_state == "open":
            status = "[OPEN]"
        else:
            status = f"[UNKNOWN] {current_state}"

        if verbose:
            print(status)

        # 看状态是否有变化
        key = f"{repo}#{pr_num}"
        old_status = state.get(key)

        if old_status != status:
            state[key] = status
            changes.append({**item, "old": old_status, "new": status, "data": data})

    save_state(state)
    return changes


def main():
    parser = argparse.ArgumentParser(description="PR Merge 监控")
    parser.add_argument("--watch", action="store_true", help="持续监控模式")
    parser.add_argument("--interval", type=int, default=30, help="检查间隔（分钟），默认 30")
    args = parser.parse_args()

    # 如果没有 token，提示但继续
    global TOKEN
    if not TOKEN:
        print("[WARN] 未设置 GITHUB_TOKEN 环境变量，API 有频率限制（60次/小时）")
        print("  建议设置: set GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx\n")

    if args.watch:
        print(f"[Watch] 启动持续监控，每 {args.interval} 分钟检查一次 (Ctrl+C 停止)\n")
        # 先执行一次
        changes = run_once(True)
        for c in changes:
            url = f"https://github.com/{c['repo']}/pull/{c['pr']}"
            if "MERGED" in c["new"]:
                notify_all(
                    "PR Merge 通知",
                    f"PR #{c['pr']} - {c['title']}\n{c['repo']}\n{url}",
                )
            elif "CLOSED" in c["new"]:
                notify_all(
                    "PR 已关闭",
                    f"PR #{c['pr']} - {c['title']}\n{c['repo']}\n{url}（未合并）",
                )

        while True:
            try:
                time.sleep(args.interval * 60)
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 检查中...")
                changes = run_once(True)
                for c in changes:
                    url = f"https://github.com/{c['repo']}/pull/{c['pr']}"
                    if "MERGED" in c["new"]:
                        notify_all(
                            "PR Merge 通知",
                            f"PR #{c['pr']} - {c['title']}\n{c['repo']}\n{url}",
                        )
                    elif "CLOSED" in c["new"]:
                        notify_all(
                            "PR 已关闭",
                            f"PR #{c['pr']} - {c['title']}\n{c['repo']}\n{url}（未合并）",
                        )
            except KeyboardInterrupt:
                print("\n[Stop] 监控已停止")
                break
    else:
        print("[Info] 一次性检查 PR 状态:\n")
        changes = run_once(True)
        if not changes:
            print("\n[OK] 状态无变化。")


if __name__ == "__main__":
    main()
