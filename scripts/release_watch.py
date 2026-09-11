#!/usr/bin/env python3
"""
DEEPCODE 新版本监控 — 检测 HKUDS/DeepCode 和 lessweb/deepcode-cli
发布新版本（release/tag 变化），并在桌面上弹通知提醒去提 PR。

用法:
  python scripts/release_watch.py              # 一次性检查
  python scripts/release_watch.py --watch      # 持续监控（每 30 分钟检查一次）
  python scripts/release_watch.py --watch --interval 15  # 每 15 分钟检查一次

首次运行会记录当前版本作为基线；之后检测到版本变化即通知。
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

# ── 在这里配置你要监控的仓库 ───────────────────────────────────
WATCH_REPOS = [
    {
        "repo": "HKUDS/DeepCode",
        "label": "DEEPCODE UPSTREAM (HKUDS/DeepCode, Python)",
        "url": "https://github.com/HKUDS/DeepCode/releases",
    },
    {
        "repo": "lessweb/deepcode-cli",
        "label": "DEEPCODE CLI (lessweb/deepcode-cli, TypeScript)",
        "url": "https://github.com/lessweb/deepcode-cli/releases",
    },
]
# ───────────────────────────────────────────────────────────────

TOKEN = os.environ.get("GITHUB_TOKEN", "")
STATE_FILE = Path(__file__).resolve().parent.parent / ".release_watch_state.json"


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_latest_release(repo: str) -> dict | None:
    """通过 GitHub API 获取最新 release，返回 {tag, name, published_at, html_url}。"""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "release-watch-script",
    }
    if TOKEN:
        headers["Authorization"] = f"token {TOKEN}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {
                "tag": data.get("tag_name", ""),
                "name": data.get("name") or "",
                "published_at": (data.get("published_at") or "")[:10],
                "html_url": data.get("html_url", ""),
            }
    except Exception as e:
        print(f"  [ERROR] 获取 {repo} release 失败: {e}")
        return None


def notify_win(title: str, message: str):
    """Windows 桌面弹窗 (PowerShell MessageBox)"""
    encoded_title = title.replace("'", "''")
    encoded_msg = message.replace("'", "''")
    ps_script = (
        f"Add-Type -AssemblyName System.Windows.Forms; "
        f"[System.Windows.Forms.MessageBox]::Show('{encoded_msg}', '{encoded_title}')"
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
    """检查所有仓库，返回有新版本的仓库列表。"""
    state = load_state()
    changes = []

    for item in WATCH_REPOS:
        repo = item["repo"]
        label = item["label"]

        if verbose:
            print(f"  [..] {repo}...", end=" ", flush=True)

        release = fetch_latest_release(repo)
        if not release:
            if verbose:
                print("获取失败")
            continue

        tag = release["tag"]
        prev_tag = state.get(repo)

        if prev_tag is None:
            # 首次运行：记录基线，不通知
            state[repo] = tag
            if verbose:
                print(f"基线 v{tag} (已记录)")
        elif prev_tag != tag:
            # 检测到新版本！
            state[repo] = tag
            changes.append({"repo": repo, "label": label, "old": prev_tag, "new": tag, "release": release, "url": item["url"]})
            if verbose:
                print(f"新版本! {prev_tag} -> {tag}")
        else:
            if verbose:
                print(f"无变化 (v{tag})")

    save_state(state)
    return changes


def main():
    parser = argparse.ArgumentParser(description="DEEPCODE 新版本监控")
    parser.add_argument("--watch", action="store_true", help="持续监控模式")
    parser.add_argument("--interval", type=int, default=30, help="检查间隔（分钟），默认 30")
    parser.add_argument("--quiet", action="store_true", help="不打印例行检查输出")
    args = parser.parse_args()

    print(f"[{datetime.now().strftime('%H:%M:%S')}] DEEPCODE 新版本监控启动")
    print("  监控: " + ", ".join(item['repo'] for item in WATCH_REPOS))

    while True:
        try:
            changes = run_once(verbose=not args.quiet)
            for c in changes:
                release = c["release"]
                notify_all(
                    f"🚀 {c['label']} 发布新版本!",
                    f"{c['old']} → {c['new']}\n"
                    f"发布时间: {release['published_at']}\n"
                    f"说明: {release['name'] or '(无)'}\n\n"
                    f"快去挑刺儿提 PR → {release['html_url']}\n"
                    f"仓库: {c['url'] if 'url' in c else ''}",
                )
        except KeyboardInterrupt:
            print("\n监控已停止")
            break
        except Exception as e:
            print(f"  [ERROR] {e}")

        if not args.watch:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
