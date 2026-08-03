#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepCode 小脑 — Hooks 入口 CLI
═══════════════════════════════
供 settings.json hooks 调用:
  SessionStart → session_start (设置快照 + vault 索引)
  PostTask     → post_task --task "..." (经验提炼)
  PreCompact   → pre_compact (会话摘要)

用法:
  python cerebellum_cli.py session_start
  python cerebellum_cli.py post_task --task "修复了 XXX bug"
  python cerebellum_cli.py pre_compact --session-id abc123
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cerebellum_core import (
    CerebellumMemory,
    experience_record,
    index_vault,
    session_briefing,
    session_summarize,
    settings_snapshot,
)

EXIT_OK = 0
EXIT_ERROR = 1


def _log(msg: str) -> None:
    print(f"[cerebellum] {msg}", file=sys.stderr, flush=True)


def cmd_session_start(task_hint: str = "") -> int:
    """SessionStart: 设置快照 + 索引 vault + 生成前车之鉴简报 (小脑预热)"""
    ok = True
    try:
        r = settings_snapshot("all")
        _log(f"设置快照: {r['ok']} ({len(r['snapshots'])} 份)")
    except Exception as e:
        _log(f"设置快照失败: {e}")
        ok = False
    try:
        r = index_vault()
        _log(f"知识库索引: {r.get('ok', False)} ({r.get('indexed_notes', 0)} 篇)")
    except Exception as e:
        _log(f"vault 索引失败: {e}")
        ok = False
    try:
        r = session_briefing(task_hint)
        n = r.get("count", 0)
        brief = r.get("briefing", "")[:60]
        _log(f"前车之鉴简报: {n} 条经验 | {brief}...")
    except Exception as e:
        _log(f"简报生成失败: {e}")
        ok = False
    return EXIT_OK if ok else EXIT_ERROR


def cmd_post_task(task: str) -> int:
    """PostTask: 用本地模型提炼经验"""
    if not task:
        _log("跳过: 无任务描述")
        return EXIT_OK
    try:
        r = experience_record(task)
        _log(f"经验已沉淀: {r['lesson'][:80]}")
        return EXIT_OK
    except Exception as e:
        _log(f"经验提炼失败: {e}")
        return EXIT_ERROR


def _read_session_context(session_id: str, max_chars: int = 4000) -> str:
    """PreCompact: 自动读取当前会话 jsonl 提取 user/assistant 对话内容。

    查找顺序:
      1. 用户主目录 ~/.deepcode/projects/<project>/ 下与 session_id 同名的 .jsonl
      2. 该目录下 mtime 最新的 .jsonl (排除 sessions-index.json 等非会话文件)
      3. stdin 非空时优先用 stdin 传入的原始对话文本
    返回截断到 max_chars 的对话文本; 找不到时返回 "" (由 session_summarize 空输入防护兜底)。
    """
    # 1) 显式 stdin 优先 (hook 可把原始对话通过管道传入)
    try:
        if not sys.stdin.isatty():
            data = sys.stdin.read()
            if data and data.strip():
                return data.strip()[:max_chars]
    except Exception:
        pass

    projects = Path.home() / ".deepcode" / "projects"
    if not projects.is_dir():
        return ""
    candidates = []
    for proj in sorted(projects.iterdir()):
        if not proj.is_dir():
            continue
        for f in proj.glob("*.jsonl"):
            if f.name == "sessions-index.json":
                continue
            if session_id != "adhoc" and session_id in f.name:
                candidates.append(f)
        if session_id == "adhoc":
            # 非显式 session: 收集全部候选后按 mtime 取最新
            candidates.extend(f for f in proj.glob("*.jsonl") if f.name != "sessions-index.json")
    if not candidates:
        return ""
    # 同名 session 优先; 否则 mtime 最新
    cand = candidates[0] if len(candidates) == 1 else max(candidates, key=lambda f: f.stat().st_mtime)
    try:
        lines = []
        with open(cand, "r", encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    obj = json.loads(ln)
                except Exception:
                    continue
                role = obj.get("role", "")
                content = obj.get("content", "")
                if role in ("user", "assistant") and isinstance(content, str) and content.strip():
                    text = content.strip()
                    # 截断超长单条消息, 保留头尾
                    if len(text) > 600:
                        text = text[:400] + "\n...[中略]...\n" + text[-150:]
                    lines.append(f"[{role}] {text}")
                if sum(len(l) for l in lines) >= max_chars:
                    break
        joined = "\n".join(lines)
        return joined[:max_chars]
    except Exception as e:
        _log(f"读取会话文件失败: {e}")
        return ""


def cmd_pre_compact(session_id: str = "adhoc") -> int:
    """PreCompact: 自动读取最近会话上下文, 本地模型生成真实摘要"""
    try:
        context = _read_session_context(session_id)
        r = session_summarize(context, session_id)
        _log(f"会话摘要已持久化 (session={session_id}, 上下文 {len(context)} 字符)")
        return EXIT_OK
    except Exception as e:
        _log(f"会话摘要失败: {e}")
        return EXIT_ERROR


def main() -> int:
    ap = argparse.ArgumentParser(prog="cerebellum_cli", description="DeepCode 小脑 hooks 入口")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ss = sub.add_parser("session_start", help="SessionStart: 设置快照 + vault 索引 + 简报")
    p_ss.add_argument("--task-hint", default="", help="本次会话任务提示(用于语义检索经验)")
    p_pt = sub.add_parser("post_task", help="PostTask: 经验提炼")
    p_pt.add_argument("--task", default="")
    p_pc = sub.add_parser("pre_compact", help="PreCompact: 会话摘要")
    p_pc.add_argument("--session-id", default="adhoc")

    args = ap.parse_args()
    if args.cmd == "session_start":
        return cmd_session_start(getattr(args, "task_hint", ""))
    if args.cmd == "post_task":
        return cmd_post_task(args.task)
    if args.cmd == "pre_compact":
        return cmd_pre_compact(args.session_id)
    ap.print_help()
    return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
