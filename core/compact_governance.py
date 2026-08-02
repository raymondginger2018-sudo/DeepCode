#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CompactGovernance v1.0 — 压缩治理层（接线增强）
=====================================================
定位: 把 AgentRunner 内置压缩（_maybe_compact）产生的摘要变成
      可检索的历史资产, 并在后续轮次按需注入相关片段。

为什么需要它:
  - AgentRunner 的 _maybe_compact 只生成一段散文摘要, 压缩后历史细节
    永久丢失, 用户再问"之前查到那个数据"就找不回来了。
  - 本模块在 turn 结束时检测压缩边界 → 把摘要 + 历史 user 消息写入
    SQLite (compact_vault.db), 下次 UserInput 时用 TF-IDF 检索相关
    片段注入上下文。零额外模型调用, 纯增强, 失败静默降级。

架构:
  HistoryVault        — SQLite 存储 + jieba TF-IDF 余弦检索
  vault_after_turn    — turn 结束后扫描 history, 检测内置压缩摘要并入库
  retrieve_context    — 按当前问题检索历史片段, 生成注入文本
  structured_summarize — 用 provider 把文本摘要为 JSON 结构化信息
                         (供 compact_engine 的 deep 级别复用)

用法:
  vault = HistoryVault()                       # ~/.deepcode/compact_vault.db
  n = vault_after_turn(vault, history, "sess-1")
  ctx = retrieve_context(vault, "主力资金流向", top_k=3)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# AgentRunner._SUMMARY_PREFIX 的识别锚点（内置压缩摘要的 user 消息特征）
SUMMARY_PREFIX_ANCHOR = "An earlier agent worked on this task"
SUMMARY_PREFIX_LABEL = "CONTEXT CHECKPOINT COMPACTION"

_VAULT_LOCK = threading.RLock()
_tokenize_cache: dict[str, list[str]] = {}


# ══════════════════════════════════════════════
# 分词 — jieba 优先, bigram 回退 (零依赖)
# ══════════════════════════════════════════════

def tokenize(text: str) -> list[str]:
    """中文/代码混合分词: jieba 优先, 失败回退到字符 bigram。"""
    text = (text or "").lower()
    if not text:
        return []
    if len(text) > 4000:
        text = text[:2000] + text[-2000:]  # 超长文本截取首尾, 防内存爆
    cached = _tokenize_cache.get(text)
    if cached is not None:
        return cached
    try:
        import jieba

        jieba.setLogLevel(60)  # 静默 jieba 初始化日志
        words = [w for w in jieba.cut(text) if len(w.strip()) >= 1]
    except Exception:
        # 回退: 英文单词 + 数字 + 中文 bigram
        words = re.findall(r"[a-z0-9_]+", text)
        cn = re.findall(r"[\u4e00-\u9fff]", text)
        words += [cn[i] + cn[i + 1] for i in range(len(cn) - 1)]
    words = [w for w in words if w.strip()]
    if len(words) > 4000:
        words = words[:4000]
    _tokenize_cache[text] = words
    return words


# ══════════════════════════════════════════════
# HistoryVault — SQLite 历史索引
# ══════════════════════════════════════════════

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'user',   -- user | summary | fact
    content     TEXT NOT NULL,
    tokens      TEXT NOT NULL,                  -- JSON 数组
    content_hash TEXT NOT NULL UNIQUE,          -- 去重
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_session ON chunks(session_key);
CREATE INDEX IF NOT EXISTS idx_chunks_kind ON chunks(kind);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _default_vault_path() -> str:
    home = os.environ.get("HOME", os.environ.get("USERPROFILE", ""))
    return str(Path(home) / ".deepcode" / "compact_vault.db")


class HistoryVault:
    """压缩历史向量库 — SQLite + TF-IDF 余弦检索。

    线程安全（进程内 RLock）; 失败时所有方法返回空结果, 绝不抛异常。
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or os.environ.get(
            "DEEPCODE_VAULT_DB", _default_vault_path()
        )
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        try:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            with _VAULT_LOCK:
                conn = self._connect()
                try:
                    conn.executescript(_SCHEMA)
                    conn.commit()
                finally:
                    conn.close()
        except Exception:
            pass  # 库不可写则整个功能静默降级

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── 写入 ──

    def add(self, session_key: str, kind: str, content: str) -> int:
        """写一条 chunk, 返回插入行数 (0=重复/失败)。"""
        content = (content or "").strip()
        if not content:
            return 0
        tokens = tokenize(content)
        if not tokens:
            return 0
        c_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]
        try:
            with _VAULT_LOCK:
                conn = self._connect()
                try:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO chunks "
                        "(session_key, kind, content, tokens, content_hash, created_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (session_key, kind, content,
                         json.dumps(tokens[:3000], ensure_ascii=False),
                         c_hash, self._now()),
                    )
                    conn.commit()
                    return cur.rowcount
                finally:
                    conn.close()
        except Exception:
            return 0

    def add_summary(self, session_key: str, summary: str) -> int:
        return self.add(session_key, "summary", summary)

    def bulk_add(self, session_key: str, kind: str, contents: list[str]) -> int:
        n = 0
        for c in contents:
            n += self.add(session_key, kind, c)
        return n

    # ── 检索 ──

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """TF-IDF 余弦检索, 返回 [{id, session_key, kind, content, score}]。"""
        if not query:
            return []
        try:
            with _VAULT_LOCK:
                conn = self._connect()
                try:
                    rows = conn.execute(
                        "SELECT id, session_key, kind, content, tokens "
                        "FROM chunks ORDER BY id DESC LIMIT 2000"
                    ).fetchall()
                finally:
                    conn.close()
        except Exception:
            return []
        if not rows:
            return []

        q_tokens = tokenize(query)
        if not q_tokens:
            return []

        # 词频 + 文档频率
        doc_tf: list[dict[str, int]] = []
        df: dict[str, int] = {}
        for r in rows:
            try:
                toks: list[str] = json.loads(r["tokens"])
            except Exception:
                toks = []
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            doc_tf.append(tf)
            for t in set(tf):
                df[t] = df.get(t, 0) + 1

        n_docs = len(doc_tf)
        idf = {t: max(1.0, (n_docs + 1) / (df[t] + 1)) for t in df}
        q_tf: dict[str, int] = {}
        for t in q_tokens:
            q_tf[t] = q_tf.get(t, 0) + 1

        def _norm(tf: dict[str, int]) -> float:
            return sum((c * idf.get(t, 1.0)) ** 2 for t, c in tf.items()) ** 0.5

        q_vec = {t: c * idf.get(t, 1.0) for t, c in q_tf.items()}
        q_norm = _norm(q_vec)
        if q_norm == 0:
            return []

        scored: list[tuple[float, int]] = []
        for i, tf in enumerate(doc_tf):
            dot = sum(q_vec.get(t, 0.0) * c * idf.get(t, 1.0)
                      for t, c in tf.items())
            if dot <= 0:
                continue
            d_norm = _norm(tf)
            if d_norm == 0:
                continue
            scored.append((dot / (q_norm * d_norm), i))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, i in scored[:top_k]:
            r = rows[i]
            results.append({
                "id": r["id"],
                "session_key": r["session_key"],
                "kind": r["kind"],
                "content": r["content"],
                "score": round(score, 4),
            })
        return results

    # ── 更新 / 删除 (对齐 MEM0 add/update/delete 三操作) ──

    def update(self, chunk_id: int, content: str) -> bool:
        """按 id 更新 chunk 内容 — 重建 tokens 与 content_hash。

        用于修正错误入库的历史片段 (如摘要被编辑后同步刷新)。
        返回是否实际更新 (False = id 不存在 / 内容为空 / 失败)。
        """
        content = (content or "").strip()
        if not content:
            return False
        tokens = tokenize(content)
        if not tokens:
            return False
        c_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]
        try:
            with _VAULT_LOCK:
                conn = self._connect()
                try:
                    cur = conn.execute(
                        "UPDATE chunks SET content=?, tokens=?, content_hash=? "
                        "WHERE id=?",
                        (content, json.dumps(tokens[:3000], ensure_ascii=False),
                         c_hash, chunk_id),
                    )
                    conn.commit()
                    return cur.rowcount > 0
                finally:
                    conn.close()
        except Exception:
            return False

    def delete(self, chunk_id: int | None = None,
               session_key: str | None = None) -> int:
        """删除 chunk — 按 id 或按 session_key 批量删除。

        返回删除行数 (0 = 无匹配/失败)。
        """
        if chunk_id is None and session_key is None:
            return 0
        try:
            with _VAULT_LOCK:
                conn = self._connect()
                try:
                    if chunk_id is not None:
                        cur = conn.execute(
                            "DELETE FROM chunks WHERE id=?", (chunk_id,))
                    else:
                        cur = conn.execute(
                            "DELETE FROM chunks WHERE session_key=?",
                            (session_key,))
                    conn.commit()
                    return cur.rowcount
                finally:
                    conn.close()
        except Exception:
            return 0

    # ── DeepSeek 上下文缓存监控 ──

    def record_cache(self, hit_tokens: int, miss_tokens: int) -> None:
        """累计记录 DeepSeek 缓存命中/未命中 tokens (前缀稳定优化的效果监控)。

        数据来源: API 响应 usage.prompt_cache_hit_tokens / prompt_cache_miss_tokens。
        """
        hit = max(0, int(hit_tokens or 0))
        miss = max(0, int(miss_tokens or 0))
        if hit == 0 and miss == 0:
            return
        try:
            with _VAULT_LOCK:
                conn = self._connect()
                try:
                    for key, delta in (("cache_hit_total", hit),
                                       ("cache_miss_total", miss)):
                        conn.execute(
                            "INSERT INTO meta(key, value) VALUES(?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET "
                            "value = CAST(value AS INTEGER) + ?",
                            (key, str(delta), str(delta)),
                        )
                    conn.commit()
                finally:
                    conn.close()
        except Exception:
            pass

    def cache_stats(self) -> dict[str, Any]:
        """缓存命中统计: {hit, miss, hit_rate, requests}。"""
        try:
            with _VAULT_LOCK:
                conn = self._connect()
                try:
                    rows = dict(conn.execute(
                        "SELECT key, value FROM meta "
                        "WHERE key LIKE 'cache_%'").fetchall())
                finally:
                    conn.close()
        except Exception:
            return {"hit": 0, "miss": 0, "hit_rate": 0.0, "requests": 0}
        hit = int(rows.get("cache_hit_total", 0) or 0)
        miss = int(rows.get("cache_miss_total", 0) or 0)
        total = hit + miss
        return {
            "hit": hit,
            "miss": miss,
            "hit_rate": round(hit / total, 4) if total else 0.0,
            "requests": max(1, int(rows.get("cache_requests", 0) or 0))
            if total else 0,
        }

    # ── 状态 ──

    def stats(self) -> dict[str, Any]:
        try:
            with _VAULT_LOCK:
                conn = self._connect()
                try:
                    total = conn.execute(
                        "SELECT COUNT(*) FROM chunks").fetchone()[0]
                    by_kind = dict(conn.execute(
                        "SELECT kind, COUNT(*) FROM chunks GROUP BY kind"
                    ).fetchall())
                    sessions = conn.execute(
                        "SELECT COUNT(DISTINCT session_key) FROM chunks"
                    ).fetchone()[0]
                finally:
                    conn.close()
            return {"total_chunks": total, "by_kind": by_kind,
                    "sessions": sessions, "db": self.db_path}
        except Exception:
            return {"total_chunks": 0, "by_kind": {}, "sessions": 0,
                    "db": self.db_path}


# ══════════════════════════════════════════════
# turn 结束入库 — 检测内置压缩摘要
# ══════════════════════════════════════════════

def _is_compaction_summary(msg: dict[str, Any]) -> bool:
    """内置压缩摘要: user 消息, 以 _SUMMARY_PREFIX 锚点开头。"""
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if not isinstance(content, str):
        return False
    return content.startswith(SUMMARY_PREFIX_ANCHOR) or (
        SUMMARY_PREFIX_LABEL in content[:200]
    )


def vault_after_turn(
    vault: Optional[HistoryVault],
    history: list[dict[str, Any]],
    session_key: str,
) -> int:
    """turn 结束后入库压缩摘要与其覆盖的历史 user 消息。

    返回入库 chunk 数 (0=本次无压缩或写入失败)。
    扫描 history: 若存在内置压缩摘要, 将摘要 + 摘要之前的所有 user
    消息写入 vault, 使压缩掉的历史成为可检索资产。
    """
    if vault is None or not history:
        return 0
    try:
        summary_idx = next(
            (i for i, m in enumerate(history)
             if _is_compaction_summary(m)),
            None,
        )
        if summary_idx is None:
            return 0
        n = 0
        # 摘要之前的 user 消息 (压缩覆盖的历史内容)
        user_texts = [
            str(history[i].get("content", ""))
            for i in range(summary_idx)
            if history[i].get("role") == "user"
            and isinstance(history[i].get("content"), str)
            and not _is_compaction_summary(history[i])
        ]
        n += vault.bulk_add(session_key, "user", user_texts)
        # 摘要本身作为 summary chunk
        n += vault.add_summary(session_key, str(history[summary_idx].get("content", "")))
        return n
    except Exception:
        return 0


# ══════════════════════════════════════════════
# 检索注入 — 生成可读的注入上下文
# ══════════════════════════════════════════════

_RETRIEVAL_HEADER = "[历史记忆检索] 以下是与当前任务相关的历史片段 (压缩自早前对话):"


def retrieve_context(
    vault: Optional[HistoryVault],
    query: str,
    top_k: int = 3,
    max_chars: int = 1500,
) -> str:
    """按 query 检索历史, 返回注入文本; 无结果返回空串。"""
    if vault is None or not query:
        return ""
    results = vault.search(query, top_k=top_k)
    if not results:
        return ""
    parts = [_RETRIEVAL_HEADER]
    used = 0
    for r in results:
        snippet = r["content"].strip().replace("\n", " ")[:400]
        if not snippet:
            continue
        line = f"- [{r['kind']}·相关性{r['score']:.2f}] {snippet}"
        if used + len(line) > max_chars:
            break
        parts.append(line)
        used += len(line)
    if len(parts) == 1:
        return ""
    return "\n".join(parts)


# ══════════════════════════════════════════════
# 结构化摘要 — provider 生成 JSON 结构化信息
# ══════════════════════════════════════════════

_STRUCTURED_PROMPT = """你是上下文压缩助手。将以下对话历史压缩为 JSON 结构化信息, 用于长期记忆检索。

输出必须是合法 JSON, 结构如下:
{
  "summary": "一句话总体摘要 (中文, ≤80字)",
  "facts": ["关键事实/数据, 每条一行 (中文)"],
  "decisions": ["已做的决策, 每条一行 (中文)"],
  "files": ["涉及的文件路径/代码位置"],
  "tasks": ["未完成的待办/下一步"]
}

规则:
1. 只输出 JSON, 不要 markdown 代码块, 不要任何其他文字
2. facts/decisions/tasks 每项 ≤60 字, 总共不超过 20 项
3. 保留数字、文件路径、股票代码等关键信息
4. 没有的内容用空数组 []

历史文本:
---
{text}
---
JSON:"""


def structured_summarize(
    provider: Any,
    model: str,
    text: str,
    max_input_chars: int = 12000,
    max_tokens: int = 800,
) -> dict[str, Any]:
    """用 provider 将文本摘要为 JSON 结构化信息。

    任何失败都降级为 {"summary": 原文截断}, 不抛异常。
    provider 需支持 chat_with_retry(messages=..., model=..., tools=None)。
    """
    empty = {"summary": "", "facts": [], "decisions": [], "files": [], "tasks": []}
    text = (text or "").strip()
    if not text:
        return empty
    if len(text) > max_input_chars:
        text = text[: max_input_chars // 2] + "\n...[中略]...\n" + text[-max_input_chars // 2:]

    request = [{"role": "user", "content": _STRUCTURED_PROMPT.replace("{text}", text)}]
    import asyncio

    try:
        kwargs: dict[str, Any] = {
            "messages": request,
            "model": model,
            "tools": None,
            "retry_mode": "fast",
        }
        if hasattr(provider, "generation") and provider.generation:
            if getattr(provider.generation, "max_tokens", None):
                kwargs["max_tokens"] = max_tokens
        response = _run_provider_chat(provider, kwargs, asyncio)
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            return empty
        parsed = _parse_structured(content)
        if parsed:
            return parsed
        # 解析失败: 降级为文本摘要
        return {"summary": content.strip()[:500], "facts": [], "decisions": [],
                "files": [], "tasks": []}
    except Exception:
        return empty


def _run_provider_chat(provider: Any, kwargs: dict[str, Any],
                       asyncio_module: Any) -> Any:
    """调用 provider.chat_with_retry (async), 兼容已运行事件循环的调用方。"""
    async def _call() -> Any:
        return await provider.chat_with_retry(**kwargs)

    try:
        return asyncio_module.run(_call())
    except RuntimeError:
        # 调用方已处于事件循环中 (如 AgentSession 内部), 改用 run_until_complete
        loop = asyncio_module.get_event_loop()
        return loop.run_until_complete(_call())


def _parse_structured(content: str) -> Optional[dict[str, Any]]:
    """宽容解析 LLM 输出的 JSON (容忍 ```json 包裹/前后缀)。"""
    text = content.strip()
    # 剥 markdown 代码块
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    result = {
        "summary": str(data.get("summary", ""))[:300],
        "facts": [str(x)[:120] for x in data.get("facts", []) if x],
        "decisions": [str(x)[:120] for x in data.get("decisions", []) if x],
        "files": [str(x)[:160] for x in data.get("files", []) if x],
        "tasks": [str(x)[:120] for x in data.get("tasks", []) if x],
    }
    if not any(result.values()):
        return None
    return result


# ══════════════════════════════════════════════
# ══════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="CompactGovernance v1.0")
    p.add_argument("--stats", action="store_true", help="查看 vault 统计")
    p.add_argument("--query", type=str, help="检索历史")
    p.add_argument("--db", type=str, help="vault db 路径")
    p.add_argument("--top-k", type=int, default=3)
    args = p.parse_args()

    v = HistoryVault(db_path=args.db)
    if args.stats:
        print(json.dumps(v.stats(), indent=2, ensure_ascii=False))
    elif args.query:
        for r in v.search(args.query, top_k=args.top_k):
            print(f"[{r['score']:.3f}] ({r['kind']}) {r['content'][:120]}")
    else:
        print(json.dumps(v.stats(), indent=2, ensure_ascii=False))
