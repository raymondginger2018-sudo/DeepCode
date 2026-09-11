#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
④ 日志异常分类器 — 本地 qwen 分类 ERROR/WARN 日志 (类别/严重度/建议)
════════════════════════════════════════════════════════════════════
用法:
  # 分类单个日志文件
  python log_classifier.py --file app.log

  # 扫描目录下所有 *.log
  python log_classifier.py --dir ./logs

  # 从管道读入 (tail -f 场景)
  tail -100 app.log | python log_classifier.py

  # 输出 JSON (供程序消费 / onError hook)
  python log_classifier.py --file app.log --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ollama_client import ensure_ollama, generate, LLM_MODEL

MAX_ITEMS = 20  # 一次最多分类的日志条数 (本地模型上下文有限)

# 日志级别 / 异常特征
LEVEL_RE = re.compile(r"\b(ERROR|WARN|WARNING|FATAL|CRITICAL)\b", re.I)
TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\)|Exception|Error:", re.I)
TIMESTAMP_RE = re.compile(r"\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}")
GARBAGE_RE = re.compile(r"^[\s\-_=|~]{1,40}$")  # 分隔线等噪声


def extract_entries(text: str) -> list[dict]:
    """提取日志条目: {line, level, message, count}"""
    lines = text.splitlines()
    entries = []
    cur = None
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or GARBAGE_RE.match(stripped):
            continue
        lv = LEVEL_RE.search(stripped)
        is_err = bool(lv) or TRACEBACK_RE.search(stripped)
        if is_err and (lv or len(stripped) > 20):
            # 新错误开始
            if cur:
                entries.append(cur)
            cur = {"line": i, "level": (lv.group(1).upper() if lv else "ERROR"),
                   "message": stripped[:300], "trace": []}
        elif cur and (stripped.startswith(("  ", "\t", "File ")) or stripped.startswith("at ")):
            cur["trace"].append(stripped[:200])
        elif not cur and is_err:
            cur = {"line": i, "level": (lv.group(1).upper() if lv else "ERROR"),
                   "message": stripped[:300], "trace": []}
        elif cur and len(cur["trace"]) > 6:
            cur["trace"] = cur["trace"][:6]
    if cur:
        entries.append(cur)

    # 按消息去重合并 (同一条错误重复出现 → count)
    dedup: dict[str, dict] = {}
    for e in entries:
        key = e["message"][:120]
        if key in dedup:
            dedup[key]["count"] += 1
        else:
            e["count"] = 1
            dedup[key] = e
    return list(dedup.values())[:MAX_ITEMS]


def classify_entries(entries: list[dict]) -> list[dict]:
    """qwen 分类: 每条的 类别/严重度/处置建议"""
    if not entries:
        return []
    items = []
    for i, e in enumerate(entries, start=1):
        msg = e["message"].replace("\n", " ")
        items.append(f"[{i}] [{e['level']}] {msg}")
    prompt = (
        "对下面的日志错误/告警逐条分类。输出 JSON 数组，每项对应一条日志:\n"
        '[{"idx": 1, "category": "类别(2-6字, 如: 数据库/网络/权限/配置/业务/系统)", '
        '"severity": "high|medium|low", "suggestion": "处置建议(一句话中文)"}]\n'
        "要求: idx 必须与输入序号一一对应，不要输出 JSON 以外的内容。\n\n"
        + "\n".join(items)
    )
    out = generate(prompt, model=LLM_MODEL, temperature=0.2)
    # 提取 JSON 数组
    m = re.search(r"\[.*\]", out, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except Exception:
        return []
    result = []
    for item in data:
        if isinstance(item, dict) and "idx" in item:
            idx = int(item["idx"]) - 1
            if 0 <= idx < len(entries):
                result.append({
                    "entry": entries[idx],
                    "category": str(item.get("category", "未分类")),
                    "severity": str(item.get("severity", "medium")),
                    "suggestion": str(item.get("suggestion", "")),
                })
    return result


def render_report(results: list[dict]) -> str:
    sev = {"high": "🔴", "medium": "🟠", "low": "🟡"}
    lines = []
    for r in results:
        e = r["entry"]
        lines.append(f"{sev.get(r['severity'], '⚪')} [{e['line']}] {r['category']} "
                     f"(x{e['count']})  {r['suggestion']}")
        lines.append(f"    {e['message'][:110]}")
    if not lines:
        return "✅ 未发现明显异常日志。"
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(prog="log_classifier", description="本地 qwen 分类日志异常")
    ap.add_argument("--file", default="", help="日志文件")
    ap.add_argument("--dir", default="", help="扫描目录 (*.log/*.txt)")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    text = ""
    if args.file:
        p = Path(args.file)
        if not p.exists():
            print(f"[log_classifier] ❌ 文件不存在: {p}", file=sys.stderr)
            return 1
        text = p.read_text(encoding="utf-8", errors="replace")
    elif args.dir:
        parts = []
        for f in sorted(Path(args.dir).rglob("*.log")) + sorted(Path(args.dir).rglob("*.txt")):
            try:
                parts.append(f.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass
        text = "\n".join(parts)
    else:
        text = sys.stdin.read()

    if not text.strip():
        print("[log_classifier] ℹ️ 无输入日志。", file=sys.stderr)
        return 0

    entries = extract_entries(text)
    if not entries:
        print("[log_classifier] ✅ 未提取到 ERROR/WARN 级别日志。", file=sys.stderr)
        return 0

    if not ensure_ollama():
        # Ollama 不可用时仍给出规则级统计
        print(f"[log_classifier] ⚠️ Ollama 不可用，仅输出级别统计: "
              f"{dict(Counter(e['level'] for e in entries))}", file=sys.stderr)
        return 0

    results = classify_entries(entries)
    if args.json:
        out = [{
            "line": r["entry"]["line"],
            "level": r["entry"]["level"],
            "count": r["entry"]["count"],
            "category": r["category"],
            "severity": r["severity"],
            "suggestion": r["suggestion"],
            "message": r["entry"]["message"],
        } for r in results]
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    print(f"[log_classifier] 🔍 提取 {len(entries)} 条异常，分类结果:")
    print(render_report(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
