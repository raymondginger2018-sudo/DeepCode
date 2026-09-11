#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepCode 小脑 — 实体-关系图检索模块 (R2 双层级检索 / R5 标题感知分块)
════════════════════════════════════════════════════════════════
对标 LightRAG (HKUDS, arXiv:2410.05779) 的可借鉴项:

  R2 双层级检索 (P1):
    - entity_nodes / entity_edges 两张新表 (轻量 SQLite 图, 对标 NetworkXStorage 定位)
    - kg_extract_entities: 用 qwen2.5:3b (ollama_generate_json) 从经验/笔记抽实体-关系三元组写入表
    - kg_search: 实体名命中 → 邻边扩散 → (实体 + 关系 + 原文块) 三段返回
      (对标 operate.py::_perform_kg_search local 模式 + _get_node_data +
       _find_most_related_edges_from_entities / _find_related_text_unit_from_entities)

  R5 标题感知分块 (P2):
    - _chunk_by_headings: 按 Markdown 标题 (#{1,4}) 切块, 每块携带 heading 面包屑
      (对标 chunker/paragraph_semantic.py HeadingBlocks + chunk_schema.py::format_heading_context),
      纯本地零 LLM 成本。

设计纪律:
  - 优雅降级: Ollama 不可达 / 表为空 / 解析失败 → 返回 {"ok": False, ...} 或空结果,
    绝不抛异常破坏现有检索 (cerebellum_core._semantic_query 的既有语义余弦路径不受影响)。
  - 本模块依赖 cerebellum_core 的 Ollama 通道 / 连接 / 余弦工具;
    cerebellum_core 只在本模块内部函数里惰性 import 本模块, 避免循环导入。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 复用 cerebellum_core 的 Ollama 通道 / 连接 / 余弦 / 时态工具 (同包, 无循环导入:
# cerebellum_core 只在函数体内惰性 import 本模块)
from cerebellum_core import (
    ClockDomainError,
    DEFAULT_DB,
    LLM_MODEL,
    _connect,
    _cosine,
    _load_embedding,
    _parse_cutoff,
    _ts_after,
    ollama_embed,
    ollama_generate_json,
)

__all__ = [
    "init_kg_tables",
    "kg_tables_ready",
    "kg_stats",
    "kg_extract_entities",
    "kg_search",
    "_chunk_by_headings",
]


# ═══════════════════════════════════════════
# 表结构 (R2 实体-关系图, 幂等创建)
# ═══════════════════════════════════════════

def init_kg_tables(db_path: Path = DEFAULT_DB) -> None:
    """幂等创建实体-关系图表 (entity_nodes / entity_edges)。

    - entity_nodes: 实体节点, name 全局唯一 (去重键); 记录来源 (source_key/source)
      以便检索期沿 source_key 找回原文块。
    - entity_edges: 有向关系边, (src_id, dst_id, relation) 唯一去重;
      检索扩散时按 src_id / dst_id 双向查询 (对标 LightRAG 无向邻接语义)。
    """
    conn = _connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS entity_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,       -- 实体名 (去重键)
            type TEXT DEFAULT '',            -- 实体类型 (技术/工具/框架/模式...)
            summary TEXT DEFAULT '',         -- 实体摘要 (抽取时生成)
            embedding TEXT,                  -- 实体名向量 (JSON float list)
            source_key TEXT,                 -- 来源键 (对应 semantic_entries.source_key)
            source TEXT DEFAULT '',          -- 来源类型 (experience / note / memory)
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ent_source ON entity_nodes(source_key);

        CREATE TABLE IF NOT EXISTS entity_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            src_id INTEGER NOT NULL,
            dst_id INTEGER NOT NULL,
            relation TEXT NOT NULL,          -- 关系描述 (依赖/导致/解决/提出...)
            created_at TEXT NOT NULL,
            UNIQUE(src_id, dst_id, relation)
        );
        CREATE INDEX IF NOT EXISTS idx_edge_src ON entity_edges(src_id);
        CREATE INDEX IF NOT EXISTS idx_edge_dst ON entity_edges(dst_id);
        """
    )
    conn.commit()
    conn.close()


def kg_tables_ready(db_path: Path = DEFAULT_DB) -> bool:
    """实体图是否可用 (表存在且有节点数据) — 供检索侧判断是否启用 KG 增强。"""
    try:
        conn = _connect(db_path)
        n = conn.execute("SELECT COUNT(*) FROM entity_nodes").fetchone()[0]
        conn.close()
        return n > 0
    except Exception:
        return False


def kg_stats(db_path: Path = DEFAULT_DB) -> Dict[str, Any]:
    """实体图统计 (节点数 / 边数 / 来源分布)。表不存在时返回空统计, 不抛异常。"""
    try:
        conn = _connect(db_path)
        nodes = conn.execute("SELECT COUNT(*) FROM entity_nodes").fetchone()[0]
        edges = conn.execute("SELECT COUNT(*) FROM entity_edges").fetchone()[0]
        by_source = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT COALESCE(source, ''), COUNT(*) FROM entity_nodes GROUP BY source"
            ).fetchall()
        }
        conn.close()
        return {"nodes": nodes, "edges": edges, "by_source": by_source}
    except Exception:
        return {"nodes": 0, "edges": 0, "by_source": {}}


# ═══════════════════════════════════════════
# 实体-关系抽取 (R2, EXTRACT 角色 → qwen2.5:3b)
# ═══════════════════════════════════════════

KG_EXTRACT_SYSTEM = (
    "你是实体-关系抽取器。从给定的经验/笔记文本中抽取关键实体及它们之间的语义关系。\n"
    "实体 = 名词性概念 (技术/工具/框架/组件/配置/模式/方法/人名/项目名), 名称简短 (不超过12字)。\n"
    "关系 = 实体间的语义联系, 用 2-6 字的动词短语描述 (如 依赖/导致/解决/提出/属于/替代/配置)。\n"
    "只抽取文本中明确出现或直接蕴含的实体, 不要臆造; 实体数量 3-10 个。\n"
    "严格按 schema 输出 JSON, 不要输出 schema 以外的键。"
)

# Ollama format 强制结构化输出 (对标 LightRAG entity_extraction_use_json)
KG_EXTRACT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "src": {"type": "string"},
                    "tgt": {"type": "string"},
                    "relation": {"type": "string"},
                },
                "required": ["src", "tgt", "relation"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["entities", "relations"],
    "additionalProperties": False,
}


def kg_extract_entities(text: str, db_path: Path = DEFAULT_DB,
                        source_key: str = "", source: str = "",
                        use_llm: bool = True, max_entities: int = 20,
                        max_relations: int = 30) -> Dict[str, Any]:
    """从经验/笔记文本抽取实体-关系三元组并写入 entity_nodes / entity_edges。

    对标 LightRAG operate.py::extract_entities (每 chunk 一次 EXTRACT 角色调用):
    - 调 ollama_generate_json (qwen2.5:3b, LLM_MODEL) 输出
      {"entities": [{name, type, summary}], "relations": [{src, tgt, relation}]}
    - 实体按 name 去重 (UNIQUE + ON CONFLICT 刷新来源, 保留首次摘要/向量);
      关系两端实体必须都存在才写入 (防孤儿边)。
    - 幂等: 重复抽取同一批实体不产生重复行。

    优雅降级 (绝不抛异常):
    - 文本为空 / use_llm=False → {"ok": True, "skipped": ...} 直接返回
    - Ollama 不可达 / JSON 解析失败 → {"ok": False, "error": ..., "degraded": True}
    返回: {"ok": bool, "entities": 写入实体数, "relations": 写入关系数, ...}
    """
    init_kg_tables(db_path)
    if not text or not str(text).strip():
        return {"ok": True, "entities": 0, "relations": 0, "skipped": "empty"}
    if not use_llm:
        return {"ok": True, "entities": 0, "relations": 0, "skipped": "no_llm"}

    data = ollama_generate_json(
        f"文本:\n{str(text)[:1500]}\n\n请抽取实体与关系, 严格按 schema 输出 JSON。",
        schema=KG_EXTRACT_SCHEMA,
        model=LLM_MODEL,
        system=KG_EXTRACT_SYSTEM,
        temperature=0.2,
        max_tokens=600,
        timeout=120,
        retries=1,
        enable_thinking=False,
    )
    if data.get("ok") is False:
        # Ollama 不可用 / 解析失败 — 优雅降级, 不抛异常
        return {"ok": False, "error": str(data.get("error", "unknown")), "degraded": True}

    # 清洗实体: 去空名/去重
    cleaned: List[Dict[str, str]] = []
    seen: set = set()
    for e in (data.get("entities") or [])[:max_entities]:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        cleaned.append({
            "name": name[:80],
            "type": str(e.get("type") or "")[:40],
            "summary": str(e.get("summary") or "")[:300],
        })
    if not cleaned:
        return {"ok": True, "entities": 0, "relations": 0, "note": "无实体"}

    # 批量嵌入实体名 (一次 API 调用, 对标 _perform_kg_search 的批量预计算)
    embeds = ollama_embed([c["name"] for c in cleaned])
    now = datetime.now().isoformat(timespec="seconds")
    conn = _connect(db_path)
    try:
        id_by_name: Dict[str, int] = {}
        for ent, emb in zip(cleaned, embeds):
            emb_json = json.dumps(emb) if emb else None
            conn.execute(
                "INSERT INTO entity_nodes (name, type, summary, embedding, source_key, source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET source_key=excluded.source_key, source=excluded.source",
                (ent["name"], ent["type"], ent["summary"], emb_json,
                 source_key or None, source or "", now),
            )
            row = conn.execute("SELECT id FROM entity_nodes WHERE name=?", (ent["name"],)).fetchone()
            if row is not None:
                id_by_name[ent["name"]] = row["id"]
        # 关系写入: 两端实体必须已存在 (防孤儿边)
        edge_count = 0
        for r in (data.get("relations") or [])[:max_relations]:
            if not isinstance(r, dict):
                continue
            src = str(r.get("src") or "").strip()
            tgt = str(r.get("tgt") or "").strip()
            rel = str(r.get("relation") or "").strip()[:100]
            if src in id_by_name and tgt in id_by_name and rel:
                conn.execute(
                    "INSERT OR IGNORE INTO entity_edges (src_id, dst_id, relation, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (id_by_name[src], id_by_name[tgt], rel, now),
                )
                edge_count += 1
        conn.commit()
    except Exception as e:  # noqa: BLE001 — 写入失败降级, 不抛异常
        conn.rollback()
        conn.close()
        return {"ok": False, "error": str(e), "degraded": True}
    conn.close()
    return {"ok": True, "entities": len(cleaned), "relations": edge_count}


# ═══════════════════════════════════════════
# 双层级检索 (R2, local 模式) — 实体命中 → 邻边扩散 → 三段返回
# ═══════════════════════════════════════════

def kg_search(query: str, db_path: Path = DEFAULT_DB, limit: int = 5,
              as_of: Optional[str] = None, strict: bool = False) -> Dict[str, Any]:
    """双层级 KG 检索 — 实体名命中 → 邻边扩散 → (实体 + 关系 + 原文块) 三段返回。

    对标 LightRAG local 模式:
      ① 实体命中: query 嵌入 vs entity_nodes 嵌入余弦 (bge-m3) +
         实体名双向子串命中 (name in query / query in name, 无嵌入也能命中, 提升 sim 到 0.7)
      ② 邻边扩散: 命中实体沿 entity_edges 双向取邻接边 + 邻居实体
         (对标 _find_most_related_edges_from_entities)
      ③ 三段返回: entities(命中实体) / relations(邻接边) / chunks(实体来源原文块,
         按 (source, source_key) 回查 semantic_entries, 对标 _find_related_text_unit_from_entities)

    优雅降级 (绝不抛异常):
    - 表空 / 表不存在 / Ollama 不可达 → {"ok": True, "used": False, ...} 空结果
    - strict=True 且命中实体晚于 as_of → 抛 ClockDomainError (与既有语义检索口径一致)
    """
    init_kg_tables(db_path)
    cutoff = _parse_cutoff(as_of)
    conn = _connect(db_path)
    try:
        node_count = conn.execute("SELECT COUNT(*) FROM entity_nodes").fetchone()[0]
    except Exception:
        conn.close()
        return {"ok": True, "used": False, "entities": [], "relations": [], "chunks": []}
    if node_count == 0:
        conn.close()
        return {"ok": True, "used": False, "entities": [], "relations": [], "chunks": []}

    q_embed = ollama_embed([query])[0]  # Ollama 不可达 → [] → 仅靠实体名子串命中
    ql = (query or "").lower()
    try:
        nodes = conn.execute(
            "SELECT id, name, type, summary, embedding, source_key, source, created_at "
            "FROM entity_nodes ORDER BY id DESC LIMIT 200"
        ).fetchall()
        scored: List[tuple] = []
        for r in nodes:
            sim = 0.0
            emb = _load_embedding(r["embedding"])
            if q_embed and emb:
                sim = _cosine(q_embed, emb)
            name = (r["name"] or "").lower()
            # 实体名命中: 双向子串匹配 → 强提升 (对标 local 模式 ll_keywords 命中)
            if name and ql and (name in ql or ql in name):
                sim = max(sim, 0.7)
            if sim < 0.40:
                continue
            if _ts_after(r["created_at"], cutoff):
                if strict:
                    raise ClockDomainError(
                        f"时钟域违例: KG 实体 {r['name']!r} "
                        f"(created_at={r['created_at']}) 晚于 as_of={as_of}, "
                        f"检索 {query!r} 时会泄漏未来信息"
                    )
                continue  # 非严格模式: 静默过滤
            scored.append((sim, r))
        scored.sort(key=lambda x: -x[0])
        top = scored[:max(1, limit)]

        # ② 邻边扩散: 命中实体沿边双向取邻接关系
        edges: List[Dict[str, Any]] = []
        for _sim, r in top:
            try:
                rows = conn.execute(
                    "SELECT e.src_id, e.dst_id, e.relation, e.created_at, "
                    "a.name AS src_name, b.name AS dst_name "
                    "FROM entity_edges e "
                    "JOIN entity_nodes a ON a.id = e.src_id "
                    "JOIN entity_nodes b ON b.id = e.dst_id "
                    "WHERE e.src_id=? OR e.dst_id=? ORDER BY e.id DESC LIMIT 6",
                    (r["id"], r["id"]),
                ).fetchall()
            except Exception:  # noqa: BLE001 — 边表异常不影响实体段
                rows = []
            for row in rows:
                edges.append({
                    "src": row["src_name"],
                    "relation": row["relation"],
                    "tgt": row["dst_name"],
                    "created_at": row["created_at"],
                })

        # ③ 原文块: 按 (source, source_key) 回查 semantic_entries (doc_status != 'deleted')
        chunks: List[Dict[str, Any]] = []
        seen_chunks: set = set()
        for _sim, r in top:
            if not r["source_key"]:
                continue
            try:
                row = conn.execute(
                    "SELECT content, source, source_key FROM semantic_entries "
                    "WHERE source=? AND source_key=? "
                    "AND COALESCE(doc_status, 'active') != 'deleted' "
                    "ORDER BY id DESC LIMIT 1",
                    (r["source"] or "", r["source_key"]),
                ).fetchone()
            except Exception:  # noqa: BLE001 — 旧库无 doc_status 列时降级
                row = None
            if row is not None and row["source_key"] not in seen_chunks:
                seen_chunks.add(row["source_key"])
                chunks.append({
                    "source": row["source"],
                    "source_key": row["source_key"],
                    "content": (row["content"] or "")[:300],
                })
    except ClockDomainError:
        conn.close()
        raise
    except Exception as e:  # noqa: BLE001 — 检索故障静默降级
        conn.close()
        return {"ok": True, "used": False, "error": str(e),
                "entities": [], "relations": [], "chunks": []}
    conn.close()

    entities = [
        {"name": r["name"], "type": r["type"], "summary": r["summary"],
         "similarity": round(sim, 3), "source_key": r["source_key"],
         "created_at": r["created_at"]}
        for sim, r in top
    ]
    return {
        "ok": True,
        "used": bool(entities),
        "entities": entities,
        "relations": edges,
        "chunks": chunks,
    }


# ═══════════════════════════════════════════
# R5 标题感知分块 (P 策略轻量版, 纯本地零 LLM 成本)
# ═══════════════════════════════════════════

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
_TRAILING_HASH_RE = re.compile(r"\s+#+\s*$")


def _chunk_by_headings(text: str, max_chars: int = 500) -> List[Dict[str, str]]:
    """按 Markdown 标题 (#{1,4}) 切块, 每块携带 heading 面包屑。

    对标 LightRAG P 策略 (chunker/paragraph_semantic.py HeadingBlocks +
    chunk_schema.py::format_heading_context L172):
    - 每个标题 (#~####) 开启新块, 面包屑 = 祖先标题链 (如 "## 2. 检索 → ### 2.1 双层级")
    - 标题级别回退 (## 下的新 ##) 时弹出祖先, 维护正确的层级栈
    - 代码围栏 (```) 内的 "#" 行不视为标题
    - 无标题文本 → 返回整块 [{breadcrumb: "", content: text}] (兼容"按文件整体索引"旧行为)
    - 超长块按字符降级切分 (大表/长块超限, 对标 P 策略的降级字符切分)

    返回: [{"breadcrumb": str, "content": str}, ...]
    """
    if not text or not text.strip():
        return []
    lines = text.splitlines()
    chunks: List[Dict[str, str]] = []
    stack: List[tuple] = []   # [(level, "#" * level + " " + title), ...]
    cur: List[str] = []
    cur_crumb = ""
    in_code = False

    def flush() -> None:
        nonlocal cur
        body = "\n".join(cur).strip()
        if body:
            chunks.append({"breadcrumb": cur_crumb, "content": body})
        cur = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            cur.append(line)
            continue
        if in_code:
            cur.append(line)
            continue
        m = _HEADING_RE.match(stripped)
        if m:
            flush()
            level = len(m.group(1))
            title = _TRAILING_HASH_RE.sub("", m.group(2).strip()).strip()
            # 标题栈维护: 弹出级别 >= 当前标题的祖先
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, f"{'#' * level} {title}"))
            cur_crumb = " → ".join(t for _lv, t in stack)
        else:
            cur.append(line)
    flush()

    if not chunks:
        # 只有标题没有正文 → 保持整块 (兼容旧行为)
        return [{"breadcrumb": "", "content": text}]

    # 长块降级: 超出 max_chars 按字符切分, 续块保留面包屑
    final: List[Dict[str, str]] = []
    for c in chunks:
        content = c["content"]
        if len(content) <= max_chars:
            final.append(c)
            continue
        for k in range(0, len(content), max_chars):
            final.append({"breadcrumb": c["breadcrumb"], "content": content[k:k + max_chars]})
    return final
