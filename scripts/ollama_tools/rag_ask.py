#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⑤ RAG 知识问答 — 向量化 knowledge-vault + 记忆 → 本地检索 → qwen 回答 (带来源引用)
════════════════════════════════════════════════════════════════════════════════
索引源:
  - knowledge-vault/notes/*.md (Obsidian 知识库)
  - data/memory/memory.db     (记忆条目)
  - database.db               (可选: agent_threads 等)

用法:
  # 重建索引 (首次/知识库更新后)
  python rag_ask.py --rebuild

  # 提问
  python rag_ask.py "市场温度 20260731 是什么状态"

  # 指定数据源目录
  python rag_ask.py --vault ./knowledge-vault/notes "如何配置 MCP"

  # 只看检索结果不生成回答
  python rag_ask.py --retrieve-only "缠论"
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ollama_client import cosine, embed, ensure_ollama, generate, LLM_MODEL, EMBED_MODEL

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VAULT = ROOT / "knowledge-vault/notes"
DEFAULT_MEMORY_DB = ROOT / "data/memory/memory.db"
INDEX_DB = Path(__file__).parent / "rag_index.db"

CHUNK_SIZE = 400      # 文本切块大小
CHUNK_OVERLAP = 80    # 切块重叠
TOP_K = 5             # 检索条数
SYSTEM_PROMPT = (
    "你是 DEEPCODE 本地知识助手。仅依据提供的参考资料回答，资料不足时明确说明。"
    "回答用中文，简洁准确，并引用来源。"
)


def chunk_text(text: str) -> list[str]:
    """按段落切块，保留重叠"""
    text = re.sub(r"\n{3,}", "\n\n", text)
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks, buf = [], ""
    for p in paras:
        if len(buf) + len(p) < CHUNK_SIZE:
            buf += ("\n\n" + p if buf else p)
        else:
            if buf:
                chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)
    # 超长块硬切
    out = []
    for c in chunks:
        while len(c) > CHUNK_SIZE:
            out.append(c[:CHUNK_SIZE])
            c = c[CHUNK_SIZE - CHUNK_OVERLAP:]
        if c:
            out.append(c)
    return out or [text[:CHUNK_SIZE]]


def collect_sources(vault: Path, with_memory: bool) -> list[tuple[str, str]]:
    """收集 (来源, 文本) 列表"""
    sources: list[tuple[str, str]] = []
    if vault.exists():
        for f in sorted(vault.rglob("*.md")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                if text.strip():
                    sources.append((str(f.relative_to(ROOT)), text))
            except Exception:
                pass
    if with_memory and DEFAULT_MEMORY_DB.exists():
        try:
            con = sqlite3.connect(str(DEFAULT_MEMORY_DB))
            for r in con.execute(
                "SELECT key, value FROM memory_entries "
                "UNION SELECT key, value FROM entries"):  # 兼容不同表名
                if r and r[1]:
                    sources.append((f"memory/{r[0]}", str(r[1])[:2000]))
            con.close()
        except Exception:
            pass
    return sources


def rebuild_index(vault: Path, with_memory: bool) -> int:
    sources = collect_sources(vault, with_memory)
    if not sources:
        print("[rag_ask] ⚠️ 未收集到任何资料 (vault 为空或不存在)。", file=sys.stderr)
        return 0

    texts, metas = [], []
    for src, text in sources:
        for chunk in chunk_text(text):
            texts.append(chunk)
            metas.append(src)

    # 分批向量化，避免一次请求过大
    vectors: list[list[float]] = []
    for i in range(0, len(texts), 32):
        vectors.extend(embed(texts[i:i + 32]))

    con = sqlite3.connect(str(INDEX_DB))
    con.execute("DROP TABLE IF EXISTS chunks")
    con.execute(
        "CREATE TABLE chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "source TEXT, text TEXT, emb BLOB)"
    )
    con.executemany(
        "INSERT INTO chunks (source, text, emb) VALUES (?,?,?)",
        [(metas[i], texts[i], struct.pack(f"{len(vectors[i])}f", *vectors[i]))
         for i in range(len(texts))],
    )
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    con.close()
    print(f"[rag_ask] ✅ 索引完成: {total} 个片段 / {len(sources)} 份资料 "
          f"(模型: {EMBED_MODEL})")
    return total


def search(query: str, top_k: int = TOP_K) -> list[dict]:
    con = sqlite3.connect(str(INDEX_DB))
    rows = con.execute("SELECT source, text, emb FROM chunks").fetchall()
    con.close()
    if not rows:
        return []
    qv = embed([query])[0]
    scored = [(cosine(qv, struct.unpack(f"{len(row[2]) // 4}f", row[2])), row[0], row[1])
              for row in rows]
    scored.sort(key=lambda x: -x[0])
    return [{"score": round(s, 4), "source": src, "text": txt}
            for s, src, txt in scored[:top_k]]


def ask(query: str, top_k: int) -> str:
    hits = search(query, top_k)
    if not hits:
        return "⚠️ 知识库为空，请先运行 --rebuild 建立索引。"
    ctx = "\n\n".join(
        f"[来源: {h['source']}]\n{h['text']}" for h in hits
    )
    prompt = (
        f"问题: {query}\n\n"
        f"参考资料:\n{ctx}\n\n"
        "请基于参考资料回答，并标注引用的来源文件名。"
    )
    answer = generate(prompt, model=LLM_MODEL, system=SYSTEM_PROMPT, temperature=0.3)
    sources = sorted({h["source"] for h in hits})
    return f"{answer}\n\n📎 参考来源: " + "、".join(sources)


def main() -> int:
    ap = argparse.ArgumentParser(prog="rag_ask", description="本地 RAG 知识问答")
    ap.add_argument("query", nargs="?", default="", help="问题")
    ap.add_argument("--vault", default=str(DEFAULT_VAULT), help="知识库目录")
    ap.add_argument("--no-memory", action="store_true", help="不索引记忆库")
    ap.add_argument("--rebuild", action="store_true", help="重建索引")
    ap.add_argument("--top-k", type=int, default=TOP_K, help="检索条数")
    ap.add_argument("--retrieve-only", action="store_true", help="只输出检索结果")
    args = ap.parse_args()

    if not ensure_ollama():
        return 1

    if args.rebuild or not Path(INDEX_DB).exists():
        rebuild_index(Path(args.vault), not args.no_memory)

    if not args.query:
        print("[rag_ask] ℹ️ 请输入问题，如: python rag_ask.py \"市场温度是什么状态\"")
        return 0

    if args.retrieve_only:
        for h in search(args.query, args.top_k):
            print(f"{h['score']:.3f}  {h['source']}")
            print(f"    {h['text'][:100].replace(chr(10), ' ')}")
        return 0

    print(ask(args.query, args.top_k))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
