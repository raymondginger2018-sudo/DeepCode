#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PreCompact 压缩进度条 — 检验小脑 PRECOMPACT 是否有效。

驱动 cerebellum 的【真实】PreCompact 链路, 每阶段渲染 tqdm 进度条:
  ① 读取会话上下文    → cerebellum_cli._read_session_context
  ② 语义缓存查找      → cerebellum_core._token_cache_lookup   (命中→跳过 LLM)
  ③ LLM 摘要生成      → cerebellum_core.ollama_generate       (qwen2.5:3b, 阻塞→脉冲动画)
  ④ 向量嵌入          → cerebellum_core.ollama_embed          (bge-m3)
  ⑤ 写入 SQLite       → 真实 INSERT session_summaries + 验证查询

用法:
  python precompact_progress.py                      # 真实运行 (自动取最新会话)
  python precompact_progress.py --session-id <id>    # 指定会话
  python precompact_progress.py --no-llm             # 跳过 LLM (规则摘要, 快速)
  python precompact_progress.py --demo               # 演示模式 (不依赖 Ollama)
"""
from __future__ import annotations

import argparse
import json
import sys
import time

# Windows 控制台中文编码兜底
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from tqdm import tqdm

from cerebellum_cli import _read_session_context, _last_context_error
from cerebellum_core import (
    DEFAULT_DB,
    SESSION_SYSTEM,
    LLM_MODEL,
    EMBED_MODEL,
    _connect,
    _token_cache_lookup,
    _token_cache_store,
    init_db,
    ollama_embed,
    ollama_generate,
)

# ── 五阶段权重 (合计 100) ──────────────────────────────
STAGES = [
    ("读取会话上下文", 8),
    ("语义缓存查找", 6),
    ("LLM 摘要生成", 46),
    ("向量嵌入", 25),
    ("写入 SQLite", 15),
]


class Stage:
    """单个阶段的进度条渲染 + 计时。"""

    def __init__(self, name: str, weight: int):
        self.name = name
        self.weight = weight
        self.t0 = time.time()
        self.elapsed = 0.0

    def done(self, status: str = "OK", detail: str = "") -> None:
        self.elapsed = time.time() - self.t0
        mark = {"OK": "✓", "SKIP": "·", "HIT": "⚡", "FALLBACK": "△"}.get(status, "✓")
        line = f"  {mark} {self.name}  ({self.elapsed:.1f}s)"
        if detail:
            line += f"  {detail}"
        print(line)


def pulse(total: int, label: str, max_pct: int = 95) -> None:
    """不确定进度脉冲: 缓慢爬升到 max_pct 封顶 (LLM 阻塞期用)。"""
    bar = tqdm(total=total, desc=label, ncols=72, bar_format="{l_bar}{bar}| {percentage:3.0f}%")
    pct = 0
    while pct < max_pct:
        time.sleep(0.12)
        pct = min(pct + 1, max_pct)
        bar.update(1)
    return bar


def finish_bar(bar: tqdm, total: int) -> None:
    if bar.n < total:
        bar.update(total - bar.n)
    bar.close()


def demo_run() -> int:
    """演示模式: 模拟各阶段耗时, 展示进度条效果 (不依赖 Ollama)。"""
    print("═" * 60)
    print(" 小脑 PRECOMPACT 压缩进度条 (DEMO 模式)")
    print("═" * 60)
    for name, weight in STAGES:
        s = Stage(name, weight)
        bar = tqdm(total=weight, desc=f"  {name}", ncols=72,
                   bar_format="{l_bar}{bar}| {percentage:3.0f}%")
        if name == "LLM 摘要生成":
            # 模拟 LLM 阻塞: 先脉冲动画, 再走完该阶段
            time.sleep(0.8)
            finish_bar(bar, weight)
        else:
            steps = max(1, weight // 2)
            for _ in range(steps):
                time.sleep(0.06)
                bar.update(weight // steps)
            finish_bar(bar, weight)
        s.done("OK")
    print("─" * 60)
    print(" ✓ 演示完成 (DEMO 不写入数据库)")
    print("   真实运行: python precompact_progress.py [--session-id <id>]")
    return 0


def real_run(session_id: str, use_llm: bool) -> int:
    print("═" * 60)
    print(f" 小脑 PRECOMPACT 压缩进度条  session={session_id}")
    print("═" * 60)

    # ── ① 读取会话上下文 ──────────────────────────────
    s1 = Stage(*STAGES[0])
    bar = tqdm(total=STAGES[0][1], desc="  读取会话上下文", ncols=72,
               bar_format="{l_bar}{bar}| {percentage:3.0f}%")
    context = _read_session_context(session_id)
    finish_bar(bar, STAGES[0][1])
    detail = f"{len(context)} 字符"
    if not context.strip():
        detail += f"  (空: {_last_context_error})"
    s1.done("OK", detail)

    # ── ② 语义缓存查找 ────────────────────────────────
    s2 = Stage(*STAGES[1])
    bar = tqdm(total=STAGES[1][1], desc="  语义缓存查找", ncols=72,
               bar_format="{l_bar}{bar}| {percentage:3.0f}%")
    cache_key = f"[session] {context[:4000]}" if use_llm and context.strip() else ""
    cached = _token_cache_lookup(cache_key) if cache_key else None
    finish_bar(bar, STAGES[1][1])
    if cached:
        s2.done("HIT", "缓存命中 → 免 LLM 推理")
    else:
        s2.done("OK", "未命中 → 走 LLM 生成")

    # ── ③ LLM 摘要生成 ────────────────────────────────
    s3 = Stage(*STAGES[2])
    summary = cached or ""
    if use_llm and not summary:
        if not context.strip():
            summary = ""
        else:
            bar = pulse(STAGES[2][1], "  LLM 摘要生成 (qwen2.5:3b)")
            summary = ollama_generate(
                f"对话内容:\n{context[:4000]}\n\n请压缩:", system=SESSION_SYSTEM,
                max_tokens=256, enable_thinking=False,
            )
            finish_bar(bar, STAGES[2][1])
    elif use_llm:
        s3.done("SKIP", "缓存命中, 跳过")
    else:
        s3.done("SKIP", "--no-llm, 规则摘要")

    if summary and not summary.startswith("[cerebellum") and not cached:
        s3.done("OK", f"LLM 摘要 {len(summary)} 字符")
        if cache_key:
            # 与 cerebellum_core.session_summarize 一致: 合格摘要回填语义缓存
            _token_cache_store(cache_key, summary)
    else:
        # 规则兜底 (与 session_summarize 一致)
        summary = f"[规则摘要] 会话 {session_id} · {len(context)} 字符"
        s3.done("FALLBACK", f"LLM 不可用 → 规则兜底 {len(summary)} 字符")

    # ── ④ 向量嵌入 ────────────────────────────────────
    s4 = Stage(*STAGES[3])
    bar = tqdm(total=STAGES[3][1], desc=f"  向量嵌入 ({EMBED_MODEL})", ncols=72,
               bar_format="{l_bar}{bar}| {percentage:3.0f}%")
    embed = ollama_embed([summary])[0]
    finish_bar(bar, STAGES[3][1])
    s4.done("OK" if embed else "FALLBACK", f"维度 {len(embed)}")

    # ── ⑤ 写入 SQLite + 验证 ──────────────────────────
    s5 = Stage(*STAGES[4])
    init_db(DEFAULT_DB)
    conn = _connect(DEFAULT_DB)
    before = conn.execute("SELECT COUNT(*) FROM session_summaries").fetchone()[0]
    conn.execute(
        "INSERT INTO session_summaries (session_id, summary, embedding, created_at) "
        "VALUES (?, ?, ?, ?)",
        (session_id, summary, json.dumps(embed) if embed else None,
         time.strftime("%Y-%m-%dT%H:%M:%S")),
    )
    conn.commit()
    row = conn.execute(
        "SELECT session_id, summary, created_at FROM session_summaries "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    bar = tqdm(total=STAGES[4][1], desc="  写入 SQLite", ncols=72,
               bar_format="{l_bar}{bar}| {percentage:3.0f}%")
    finish_bar(bar, STAGES[4][1])
    s5.done("OK", f"记录 {before} → {before + 1}")

    # ── 汇总: 有效性结论 ───────────────────────────────
    print("─" * 60)
    total_sec = sum(x.elapsed for x in (s1, s2, s3, s4, s5))
    src = "语义缓存⚡" if cached else ("LLM" if not summary.startswith("[规则") else "规则兜底")
    print(f" 总耗时 {total_sec:.1f}s  |  摘要来源: {src}")
    print()
    print(" 生成的会话摘要:")
    print(f"   {summary}")
    print()
    ok = bool(row and row[1])
    if ok:
        print(f" ✓ PRECOMPACT 有效: session_summaries 已落库 "
              f"(session={row[0]}, {row[2]})")
        print(f"   数据库: {DEFAULT_DB}")
        return 0
    print(" ✗ PRECOMPACT 失败: 数据库无记录")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="precompact_progress",
                                 description="PreCompact 压缩进度条 (检验小脑 PRECOMPACT)")
    ap.add_argument("--session-id", default="adhoc", help="会话 ID (默认 adhoc=最新会话)")
    ap.add_argument("--no-llm", action="store_true", help="跳过 LLM, 直接规则摘要")
    ap.add_argument("--demo", action="store_true", help="演示模式 (不依赖 Ollama)")
    args = ap.parse_args()
    if args.demo:
        return demo_run()
    return real_run(args.session_id, use_llm=not args.no_llm)


if __name__ == "__main__":
    sys.exit(main())
