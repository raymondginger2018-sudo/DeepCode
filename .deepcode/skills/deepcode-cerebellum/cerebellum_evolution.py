#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepCode 小脑 — 记忆进化引擎 (对标 MindMemOS 4 个设计点)
═══════════════════════════════════════════════════════════
1. Dreaming (离线记忆巩固): 跨会话 session_summaries 聚类 + LLM 合并 →
   consolidated_memories, 源摘要标记归档。对标 MindMemOS `memory dreaming`
   (MemoryAgentBench FactConsolidation 0.738→0.920 的关键机制)。

2. 反馈闭环: feedback_entries 表 + 检索排序加权。显式 (大脑调工具)
   + 隐式 (OnError hook 自动记录) 反馈回灌到 semantic 检索排序。

3. Skill 进化信号: 失败痕迹采集 (skill_signals) → 同 skill 失败 >= 3 次 →
   LLM 分析失败模式 → 生成 SKILL.md 更新提案 (人工确认后 apply)。

4. 评测方法论: benchmark_run 自建 QA 集 (对标 LoCoMo / PersonaMem 风格),
   输出 Recall@1 / Recall@5 / MRR 基线分, 供后续对比验证记忆系统改进。

架构: 独立模块, import cerebellum_core 的公共函数。core 只做最小改动
      (init_db 建表 + 检索加权钩子), 新逻辑全部收敛于此。
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
from cerebellum_core import (
    DEFAULT_DB,
    CerebellumMemory,
    _connect,
    _cosine,
    _load_embedding,
    _token_cache_lookup,
    _token_cache_store,
    estimate_tokens,
    init_db,
    ollama_embed,
    ollama_generate,
)

# 学习循环检测器 (10 模式, 纯规则, 只读 cerebellum.db; 不依赖 LLM)
from learning_loop_detector import detect_all as _ll_detect_all

# ═══════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════

# Dreaming 聚类阈值: 跨会话摘要相似度 >= 此值归为一簇 (经验合并阈值更高, 防误并)
DREAMING_SESSION_THRESHOLD = 0.72
DREAMING_EXPERIENCE_THRESHOLD = 0.85
# 知识级整合阈值 (刀一): 已巩固的 session/experience 精华记忆再抽象为知识,
# 源已是精华记忆, 语义更泛化, 允许更松的相似度即可归簇
DREAMING_KNOWLEDGE_THRESHOLD = 0.62

# 反馈加权系数: 净评分 +1 → similarity +0.05 (显式反馈权重 > 隐式)
FEEDBACK_WEIGHT = 0.05

# Skill 进化: 同 skill 失败信号 >= 此值触发提案
SKILL_FAILURE_TRIGGER = 3

# 评测
BENCHMARK_TOP_K = 5


# ═══════════════════════════════════════════
# schema 迁移 (新表, 全部独立, 不碰 core 现有表结构)
# ═══════════════════════════════════════════

def _init_evolution_db(db_path: Path = DEFAULT_DB) -> None:
    init_db(db_path)  # 保证 core 表存在
    conn = _connect(db_path)
    conn.executescript(
        """
        -- Dreaming 运行批次记录
        CREATE TABLE IF NOT EXISTS dreaming_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,                 -- session / experience / knowledge
            items_scanned INTEGER DEFAULT 0,
            clusters INTEGER DEFAULT 0,
            merged INTEGER DEFAULT 0,
            archived INTEGER DEFAULT 0,
            token_accounting TEXT,              -- JSON: before/after/saved tokens (改造二落库)
            created_at TEXT NOT NULL
        );

        -- 巩固后的记忆 (Dreaming 产出)
        CREATE TABLE IF NOT EXISTS consolidated_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,                 -- session / experience
            source_ids TEXT NOT NULL,           -- JSON 数组, 被合并的源 id
            content TEXT NOT NULL,
            embedding TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cons_kind ON consolidated_memories(kind);

        -- 反馈闭环 (显式 + 隐式)
        CREATE TABLE IF NOT EXISTS feedback_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type TEXT NOT NULL,          -- semantic / experience / session / memory
            target_key TEXT NOT NULL,           -- semantic.source_key 或 experience id
            rating INTEGER NOT NULL,            -- +1 有用 / -1 无用 / -2 严重错误
            source TEXT NOT NULL,               -- explicit / implicit / on_error
            comment TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_fb_target ON feedback_entries(target_type, target_key);

        -- Skill 进化信号 (失败痕迹 / 成功信号)
        CREATE TABLE IF NOT EXISTS skill_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_name TEXT NOT NULL,
            signal_type TEXT NOT NULL,          -- failure / success
            context TEXT DEFAULT '',
            error TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sig_skill ON skill_signals(skill_name, signal_type);

        -- Skill 进化提案 (pending → applied / rejected, 人工确认后 apply)
        CREATE TABLE IF NOT EXISTS skill_evolution_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_name TEXT NOT NULL,
            signal_count INTEGER DEFAULT 0,
            failure_pattern TEXT DEFAULT '',
            suggested_change TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',      -- pending / applied / rejected
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_prop_status ON skill_evolution_proposals(status);

        -- 评测运行记录 (基线分)
        CREATE TABLE IF NOT EXISTS benchmark_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            btype TEXT NOT NULL,                -- self_consistency
            metrics_json TEXT NOT NULL,
            report_path TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        -- 学习循环候选干预 (P1: learning-loop-patterns 接入, 由 detector 产出)
        CREATE TABLE IF NOT EXISTS learning_loop_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id TEXT NOT NULL,           -- repeated-rediscovery 等 10 模式
            claim_type TEXT NOT NULL,           -- opportunity / readiness / effectiveness
            severity TEXT NOT NULL,             -- Low / Medium / High
            title TEXT DEFAULT '',              -- 候选标题
            observed_behavior TEXT NOT NULL,    -- 观测行为描述
            source_episodes TEXT DEFAULT '',    -- 源 episodes (JSON 数组)
            current_cost TEXT DEFAULT '',       -- 当前代价
            broken_stage TEXT NOT NULL,         -- capture/generalize/codify/route/exercise/evaluate/maintain
            recommended_owner TEXT DEFAULT '',  -- 推荐所有者
            intervention TEXT DEFAULT '',       -- 干预建议
            provenance TEXT NOT NULL DEFAULT 'deterministic-derived',
            confidence REAL DEFAULT 0.5,        -- 0-1
            priority_score INTEGER DEFAULT 0,   -- 0-100
            status TEXT NOT NULL DEFAULT 'pending',  -- pending / applied / rejected / dismissed
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ll_status ON learning_loop_candidates(status);
        CREATE INDEX IF NOT EXISTS idx_ll_pattern ON learning_loop_candidates(pattern_id);
        """
    )
    # 旧库兼容: 无 token_accounting 列时先补 (改造二 — dreaming 记账落库)
    try:
        conn.execute("ALTER TABLE dreaming_runs ADD COLUMN token_accounting TEXT")
        conn.commit()
    except Exception:
        pass  # 列已存在 → 幂等跳过
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════
# 1. Dreaming — 离线记忆巩固
# ═══════════════════════════════════════════

_DREAMING_SYSTEM = (
    "你是 DeepCode 小脑的记忆巩固器。给你一个「同主题会话摘要簇」，"
    "请合并为一条结构化精华记忆(120字以内): 目标/关键决策/遗留事项。"
    "直接输出合并结果，不要复述原文，不要输出 JSON 标记。"
)

# 知识级整合提示词 (刀一): 输入是已巩固的 session/experience 精华记忆,
# 目标是再抽象为可跨任务复用的知识/原则/方法论 (而非摘要复述)
_DREAMING_KNOWLEDGE_SYSTEM = (
    "你是 DeepCode 小脑的知识提炼器。给你一组「同主题已巩固精华记忆」，"
    "请提炼为一条可复用的知识/原则/方法论(150字以内)，供未来同类任务直接调用。"
    "输出通用规律而非逐条复述，不要输出 JSON 标记。"
)


def _greedy_cluster(items: List[Dict], threshold: float) -> List[List[Dict]]:
    """贪心聚类: 按 embedding 相似度归簇 (簇代表 = 簇内首条)。

    简单可用的增量算法 — 数据量小 (百级), O(n²) 足够, 不引入 sklearn。
    """
    clusters: List[List[Dict]] = []
    for item in items:
        placed = False
        for cl in clusters:
            rep_embed = _load_embedding(cl[0].get("embedding"))
            item_embed = _load_embedding(item.get("embedding"))
            if rep_embed and item_embed and _cosine(rep_embed, item_embed) >= threshold:
                cl.append(item)
                placed = True
                break
        if not placed:
            clusters.append([item])
    return clusters


def _llm_merge(cluster: List[Dict], system: str = _DREAMING_SYSTEM) -> str:
    """LLM 合并一簇摘要 (qwen2.5:3b, 走 token-saver 缓存复用)。

    system: 合并提示词 — 会话/经验档用 _DREAMING_SYSTEM (合并精华记忆),
            知识档用 _DREAMING_KNOWLEDGE_SYSTEM (提炼可复用知识/原则)。
    """
    text = "\n".join(f"- {it['summary'][:200]}" for it in cluster)
    # 缓存键带 system 前缀, 避免不同提示词 (巩固 vs 提炼) 缓存互相串用
    cache_prefix = "knowledge" if system is _DREAMING_KNOWLEDGE_SYSTEM else "dreaming"
    cache_key = f"[{cache_prefix}] {text[:1500]}"
    cached = _token_cache_lookup(cache_key)
    if cached:
        return cached
    merged = ollama_generate(
        f"同主题摘要簇:\n{text}\n\n请合并为一条精华记忆:",
        system=system, max_tokens=300, enable_thinking=False,
    )
    if merged and not merged.startswith("[cerebellum"):
        _token_cache_store(cache_key, merged)
    return merged


def dreaming_run(db_path: Path = DEFAULT_DB, kind: str = "session",
                 use_llm: bool = True) -> Dict:
    """离线记忆巩固 (对标 MindMemOS Dreaming)。

    - kind="session": 整合未巩固的 session_summaries (跨会话同主题合并)
    - kind="experience": 高阈值去重 experience_entries (防误并不同经验)
    - kind="knowledge": 将已巩固的 session/experience 精华记忆再抽象为
      可跨任务复用的知识/原则 (刀一 — 梦境分层: 会话级 → 经验级 → 知识级)

    流程: 取未巩固源 → embedding 聚类 → 每簇 LLM 合并 → 写入
    consolidated_memories → 标记源已归档。
    """
    _init_evolution_db(db_path)
    conn = _connect(db_path)
    scanned, merged_count, archived = 0, 0, 0
    clusters_info: List[Dict] = []
    # token 记账: 源文本 token 总量 vs 合并后 token 量 (差值即 saved_tokens)
    tokens_before = 0
    tokens_after = 0

    if kind == "session":
        # 兼容旧库: 无 consolidated 列时先补 (core init_db 已处理, 此处双保险)
        try:
            conn.execute("ALTER TABLE session_summaries ADD COLUMN consolidated INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass
        rows = conn.execute(
            "SELECT id, session_id, summary, embedding FROM session_summaries "
            "WHERE consolidated IS NULL OR consolidated = 0 ORDER BY id"
        ).fetchall()
        items = [dict(r) for r in rows]
        scanned = len(items)
        if items:
            clusters = _greedy_cluster(items, DREAMING_SESSION_THRESHOLD)
            for cl in clusters:
                if len(cl) < 2:
                    continue
                merged = _llm_merge(cl) if use_llm else ""
                if not merged or merged.startswith("[cerebellum"):
                    merged = "；".join(i["summary"][:80] for i in cl)[:300]
                # token 记账: 合并前源摘要 token 总量 vs 合并后 token 量
                tokens_before += sum(estimate_tokens(i["summary"]) for i in cl)
                tokens_after += estimate_tokens(merged)
                embed = ollama_embed([merged])[0]
                source_ids = [str(i["id"]) for i in cl]
                conn.execute(
                    "INSERT INTO consolidated_memories (kind, source_ids, content, embedding, created_at) "
                    "VALUES ('session', ?, ?, ?, ?)",
                    (json.dumps(source_ids), merged, json.dumps(embed) if embed else None,
                     datetime.now().isoformat(timespec="seconds")),
                )
                # 标记源已归档 + 更新语义索引 (指向 consolidated)
                for i in cl:
                    conn.execute("UPDATE session_summaries SET consolidated=1 WHERE id=?", (i["id"],))
                    conn.execute(
                        "INSERT OR REPLACE INTO semantic_entries (content, source, source_key, embedding, created_at) "
                        "VALUES (?, 'consolidated', ?, ?, ?)",
                        (f"[巩固] {merged}", f"session:{i['id']}",
                         json.dumps(embed) if embed else None,
                         datetime.now().isoformat(timespec="seconds")),
                    )
                merged_count += 1
                archived += len(cl)
                clusters_info.append({
                    "source_ids": source_ids,
                    "merged": merged[:120],
                })
    elif kind == "experience":
        rows = conn.execute(
            "SELECT id, task, lesson, embedding FROM experience_entries ORDER BY id"
        ).fetchall()
        items = [dict(r) for r in rows]
        scanned = len(items)
        # 已巩固过的经验 id 集合
        done = set()
        for r in conn.execute("SELECT source_ids FROM consolidated_memories WHERE kind='experience'"):
            done.update(json.loads(r["source_ids"]))
        pending = [it for it in items if str(it["id"]) not in done]
        if pending:
            clusters = _greedy_cluster(pending, DREAMING_EXPERIENCE_THRESHOLD)
            for cl in clusters:
                if len(cl) < 2:
                    continue
                merged = _llm_merge(cl) if use_llm else ""
                if not merged or merged.startswith("[cerebellum"):
                    merged = "；".join(i["lesson"][:100] for i in cl)[:300]
                # token 记账: 合并前源经验 lesson token 总量 vs 合并后 token 量
                tokens_before += sum(estimate_tokens(i["lesson"]) for i in cl)
                tokens_after += estimate_tokens(merged)
                embed = ollama_embed([merged])[0]
                source_ids = [str(i["id"]) for i in cl]
                conn.execute(
                    "INSERT INTO consolidated_memories (kind, source_ids, content, embedding, created_at) "
                    "VALUES ('experience', ?, ?, ?, ?)",
                    (json.dumps(source_ids), merged, json.dumps(embed) if embed else None,
                     datetime.now().isoformat(timespec="seconds")),
                )
                for i in cl:
                    conn.execute(
                        "INSERT OR REPLACE INTO semantic_entries (content, source, source_key, embedding, created_at) "
                        "VALUES (?, 'consolidated', ?, ?, ?)",
                        (f"[巩固经验] {merged}", f"exp:{i['id']}",
                         json.dumps(embed) if embed else None,
                         datetime.now().isoformat(timespec="seconds")),
                    )
                merged_count += 1
                archived += len(cl)
                clusters_info.append({
                    "source_ids": source_ids,
                    "merged": merged[:120],
                })
    elif kind == "knowledge":
        # 源: 已巩固的 session/experience 精华记忆 (consolidated_memories)
        # 目标: 再抽象为可跨任务复用的知识/原则 (刀一 — 对标 MindMemOS 分层梦境)
        rows = conn.execute(
            "SELECT id, kind, source_ids, content, embedding FROM consolidated_memories "
            "WHERE kind IN ('session','experience') ORDER BY id"
        ).fetchall()
        items = []
        for r in rows:
            items.append({
                "id": r["id"],
                "summary": r["content"],   # 兼容 _llm_merge 读取 summary 字段
                "content": r["content"],
                "embedding": r["embedding"],
            })
        scanned = len(items)
        # 已提炼为知识的 consolidated 记忆 id 集合 (增量: 只处理未提炼过的)
        done = set()
        for r in conn.execute("SELECT source_ids FROM consolidated_memories WHERE kind='knowledge'"):
            done.update(json.loads(r["source_ids"]))
        pending = [it for it in items if str(it["id"]) not in done]
        if pending:
            # 源已是精华记忆, 语义更泛化, 允许更松的相似度归簇
            clusters = _greedy_cluster(pending, DREAMING_KNOWLEDGE_THRESHOLD)
            for cl in clusters:
                if len(cl) < 2:
                    continue
                merged = _llm_merge(cl, system=_DREAMING_KNOWLEDGE_SYSTEM) if use_llm else ""
                if not merged or merged.startswith("[cerebellum"):
                    merged = "；".join(i["content"][:80] for i in cl)[:300]
                # token 记账
                tokens_before += sum(estimate_tokens(i["content"]) for i in cl)
                tokens_after += estimate_tokens(merged)
                embed = ollama_embed([merged])[0]
                source_ids = [str(i["id"]) for i in cl]
                conn.execute(
                    "INSERT INTO consolidated_memories (kind, source_ids, content, embedding, created_at) "
                    "VALUES ('knowledge', ?, ?, ?, ?)",
                    (json.dumps(source_ids), merged, json.dumps(embed) if embed else None,
                     datetime.now().isoformat(timespec="seconds")),
                )
                for i in cl:
                    conn.execute(
                        "INSERT OR REPLACE INTO semantic_entries (content, source, source_key, embedding, created_at) "
                        "VALUES (?, 'consolidated', ?, ?, ?)",
                        (f"[知识] {merged}", f"knowledge:{i['id']}",
                         json.dumps(embed) if embed else None,
                         datetime.now().isoformat(timespec="seconds")),
                    )
                merged_count += 1
                archived += len(cl)
                clusters_info.append({
                    "source_ids": source_ids,
                    "merged": merged[:120],
                })
    else:
        conn.close()
        return {"ok": False, "error": f"未知 kind: {kind} (session/experience/knowledge)"}

    # 改造二: token 记账落库 (JSON), 历史节省量可从 dreaming_runs 查询
    token_acct = json.dumps({
        "before_tokens": tokens_before,
        "after_tokens": tokens_after,
        "saved_tokens": max(0, tokens_before - tokens_after),
    }, ensure_ascii=False)
    conn.execute(
        "INSERT INTO dreaming_runs (kind, items_scanned, clusters, merged, archived, token_accounting, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (kind, scanned, len(clusters_info), merged_count, archived, token_acct,
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()

    # 周期巡检: 顺带运行学习循环检测 (10 模式, 纯规则, 只读 cerebellum.db; 不依赖 LLM)
    # 候选自动持久化到 learning_loop_candidates (status=pending, 幂等去重)
    loops = {"count": 0, "persisted": []}
    try:
        ll = learning_loop_detect(db_path=db_path, min_priority=0, limit=20,
                                  persist=True)
        if ll.get("ok"):
            loops = {"count": ll.get("count", 0), "persisted": ll.get("persisted", [])}
    except Exception as e:
        loops = {"count": 0, "error": str(e)}

    return {
        "ok": True, "kind": kind, "items_scanned": scanned,
        "clusters": len(clusters_info), "merged": merged_count,
        "archived": archived, "consolidations": clusters_info,
        "learning_loops": loops,
        "token_accounting": {
            "before_tokens": tokens_before,
            "after_tokens": tokens_after,
            "saved_tokens": max(0, tokens_before - tokens_after),
        },
    }


def dreaming_history(db_path: Path = DEFAULT_DB, limit: int = 5) -> Dict:
    """查看 Dreaming 运行历史。"""
    _init_evolution_db(db_path)
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT kind, items_scanned, clusters, merged, archived, token_accounting, created_at "
        "FROM dreaming_runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return {"ok": True, "runs": [dict(r) for r in rows]}


# ═══════════════════════════════════════════
# 2. 反馈闭环 — 显式 + 隐式反馈回灌检索排序
# ═══════════════════════════════════════════

def feedback_add(target_type: str, target_key: str, rating: int,
                 source: str = "explicit", comment: str = "",
                 db_path: Path = DEFAULT_DB) -> Dict:
    """写入一条反馈。rating: +1 有用 / -1 无用 / -2 严重错误。

    source: explicit (大脑/用户显式) / implicit (行为推断) / on_error (hook 自动)。
    """
    _init_evolution_db(db_path)
    if rating not in (1, -1, -2):
        return {"ok": False, "error": f"rating 仅支持 1/-1/-2, 收到 {rating}"}
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO feedback_entries (target_type, target_key, rating, source, comment, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (target_type, str(target_key)[:200], rating, source, (comment or "")[:500],
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "target_type": target_type, "target_key": target_key,
            "rating": rating, "source": source}


def feedback_list(target_type: Optional[str] = None, target_key: Optional[str] = None,
                  db_path: Path = DEFAULT_DB, limit: int = 20) -> Dict:
    """列出反馈 (可按目标过滤)。"""
    _init_evolution_db(db_path)
    conn = _connect(db_path)
    sql = "SELECT target_type, target_key, rating, source, comment, created_at FROM feedback_entries"
    conds, params = [], []
    if target_type:
        conds.append("target_type=?")
        params.append(target_type)
    if target_key:
        conds.append("target_key=?")
        params.append(target_key)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return {"ok": True, "count": len(rows), "feedback": [dict(r) for r in rows]}


def _feedback_net_map(db_path: Path = DEFAULT_DB) -> Dict[str, int]:
    """目标键 → 净评分 (供 core 检索排序加权)。失败静默。"""
    try:
        conn = _connect(db_path)
        rows = conn.execute(
            "SELECT target_key, SUM(rating) AS net FROM feedback_entries "
            "GROUP BY target_key HAVING net != 0"
        ).fetchall()
        conn.close()
        return {r["target_key"]: r["net"] for r in rows}
    except Exception:
        return {}


def _feedback_adjust(scored: List[Dict], key_field: str = "source_key",
                     db_path: Path = DEFAULT_DB) -> List[Dict]:
    """按反馈净评分调整检索排序 (core 检索函数调用)。

    similarity += FEEDBACK_WEIGHT * net_rating, 然后重新排序。
    仅调整, 不新增/删除条目 — 外科手术式介入。
    """
    if not scored:
        return scored
    net_map = _feedback_net_map(db_path)
    if not net_map:
        return scored
    for item in scored:
        key = str(item.get(key_field) or "")
        net = net_map.get(key, 0)
        if net:
            item["similarity"] = round(float(item.get("similarity", 0)) + FEEDBACK_WEIGHT * net, 3)
    scored.sort(key=lambda x: -float(x.get("similarity", 0)))
    return scored


# ═══════════════════════════════════════════
# 3. Skill 进化信号 — 失败痕迹 → 记忆 → 更新提案
# ═══════════════════════════════════════════

def skill_signal_add(skill_name: str, signal_type: str = "failure",
                     context: str = "", error: str = "",
                     db_path: Path = DEFAULT_DB) -> Dict:
    """采集 Skill 执行信号 (OnError hook 自动调用 / 大脑显式上报)。

    signal_type: failure (执行失败) / success (执行成功)。
    """
    _init_evolution_db(db_path)
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO skill_signals (skill_name, signal_type, context, error, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (skill_name[:100], signal_type, (context or "")[:1000], (error or "")[:1000],
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    # 查询累计失败数
    cnt = conn.execute(
        "SELECT COUNT(*) FROM skill_signals WHERE skill_name=? AND signal_type='failure'",
        (skill_name,),
    ).fetchone()[0]
    conn.close()
    return {"ok": True, "skill_name": skill_name, "signal_type": signal_type,
            "failure_count": cnt}


def skill_signals_list(skill_name: Optional[str] = None, db_path: Path = DEFAULT_DB,
                       limit: int = 20) -> Dict:
    """列出 Skill 信号。"""
    _init_evolution_db(db_path)
    conn = _connect(db_path)
    if skill_name:
        rows = conn.execute(
            "SELECT skill_name, signal_type, context, error, created_at FROM skill_signals "
            "WHERE skill_name=? ORDER BY id DESC LIMIT ?", (skill_name, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT skill_name, signal_type, context, error, created_at FROM skill_signals "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return {"ok": True, "count": len(rows), "signals": [dict(r) for r in rows]}


_SKILL_PATTERN_SYSTEM = (
    "你是 DeepCode 的 Skill 诊断师。给定某 Skill 的多条失败痕迹, "
    "提炼 1-2 条「失败模式」(每条 40 字内), 并给出对 SKILL.md 的具体更新建议"
    "(修改/新增哪些规则或步骤, 60 字内)。直接输出:\n"
    "失败模式: ...\n"
    "更新建议: ...\n"
)


def skill_evolution_propose(skill_name: str, db_path: Path = DEFAULT_DB,
                            use_llm: bool = True) -> Dict:
    """同 skill 失败 >= SKILL_FAILURE_TRIGGER 次 → LLM 分析 → 生成进化提案。

    只写提案表 (status=pending), 不自动改 SKILL.md — 由大脑/用户确认后 apply。
    """
    _init_evolution_db(db_path)
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT context, error, created_at FROM skill_signals "
        "WHERE skill_name=? AND signal_type='failure' ORDER BY id DESC LIMIT 20",
        (skill_name,),
    ).fetchall()
    cnt = len(rows)
    if cnt < SKILL_FAILURE_TRIGGER:
        conn.close()
        return {"ok": True, "skill_name": skill_name, "failure_count": cnt,
                "triggered": False,
                "reason": f"失败 {cnt} 次 < 触发阈值 {SKILL_FAILURE_TRIGGER}"}

    # 防重复提案: 已有 pending 未处理则跳过
    pending = conn.execute(
        "SELECT id FROM skill_evolution_proposals WHERE skill_name=? AND status='pending'",
        (skill_name,),
    ).fetchone()
    if pending:
        conn.close()
        return {"ok": True, "skill_name": skill_name, "failure_count": cnt,
                "triggered": True, "proposal_id": pending["id"], "note": "已有待处理提案"}

    traces = "\n".join(
        f"- {r['context'][:120] or '(无上下文)'} | 错误: {r['error'][:120] or '(无)'}"
        for r in rows[:10]
    )
    pattern, suggestion = "", ""
    if use_llm:
        analysis = ollama_generate(
            f"Skill: {skill_name}\n失败痕迹:\n{traces}\n\n请诊断:",
            system=_SKILL_PATTERN_SYSTEM, max_tokens=250, enable_thinking=False,
        )
        for line in (analysis or "").splitlines():
            line = line.strip()
            if line.startswith("失败模式"):
                pattern = line.split(":", 1)[1].strip()[:300]
            elif line.startswith("更新建议"):
                suggestion = line.split(":", 1)[1].strip()[:500]
    if not pattern:
        pattern = f"多次失败 ({cnt} 次), 建议人工复核失败痕迹"
    if not suggestion:
        suggestion = "补充失败案例与规避步骤到 SKILL.md"

    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO skill_evolution_proposals "
        "(skill_name, signal_count, failure_pattern, suggested_change, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
        (skill_name, cnt, pattern, suggestion, now, now),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "skill_name": skill_name, "failure_count": cnt,
            "triggered": True, "proposal_id": cur.lastrowid,
            "failure_pattern": pattern, "suggested_change": suggestion}


def skill_evolution_list(status: Optional[str] = None, db_path: Path = DEFAULT_DB,
                         limit: int = 20) -> Dict:
    """列出进化提案。"""
    _init_evolution_db(db_path)
    conn = _connect(db_path)
    if status:
        rows = conn.execute(
            "SELECT id, skill_name, signal_count, failure_pattern, suggested_change, status, created_at "
            "FROM skill_evolution_proposals WHERE status=? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, skill_name, signal_count, failure_pattern, suggested_change, status, created_at "
            "FROM skill_evolution_proposals ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return {"ok": True, "count": len(rows), "proposals": [dict(r) for r in rows]}


def _skill_md_path(skill_name: str) -> Optional[Path]:
    """定位 Skill 的 SKILL.md。"""
    candidates = [
        Path(__file__).parent.parent / skill_name / "SKILL.md",
        Path(__file__).parent.parent.parent / "skills" / skill_name / "SKILL.md",
        Path(__file__).parent.parent.parent.parent / ".deepcode" / "skills" / skill_name / "SKILL.md",
    ]
    for p in candidates:
        if p.exists():
            return p
    # 全盘搜索 .deepcode/skills 下同名 skill
    skills_root = Path(__file__).parent.parent.parent / "skills"
    if skills_root.exists():
        for d in skills_root.iterdir():
            if d.is_dir() and d.name == skill_name:
                md = d / "SKILL.md"
                if md.exists():
                    return md
    return None


def skill_evolution_apply(proposal_id: int, db_path: Path = DEFAULT_DB) -> Dict:
    """人工确认后把提案应用到 SKILL.md (只追加「进化记录」节, 不改原文)。"""
    _init_evolution_db(db_path)
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT id, skill_name, failure_pattern, suggested_change, status "
        "FROM skill_evolution_proposals WHERE id=?", (proposal_id,)
    ).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "error": f"提案不存在: {proposal_id}"}
    if row["status"] == "applied":
        conn.close()
        return {"ok": True, "already_applied": True, "proposal_id": proposal_id}

    md_path = _skill_md_path(row["skill_name"])
    if not md_path:
        conn.close()
        return {"ok": False, "error": f"未找到 {row['skill_name']} 的 SKILL.md",
                "suggested_change": row["suggested_change"]}

    try:
        now = datetime.now().isoformat(timespec="minutes")
        section = (
            f"\n## 进化记录 (cerebellum 自动生成)\n"
            f"<!-- {now} · 提案 #{proposal_id} · 触发: {row['signal_count']} 次失败信号 -->\n"
            f"- **失败模式**: {row['failure_pattern']}\n"
            f"- **更新建议**: {row['suggested_change']}\n"
        )
        with open(md_path, "a", encoding="utf-8") as fh:
            fh.write(section)
        conn.execute(
            "UPDATE skill_evolution_proposals SET status='applied', updated_at=? WHERE id=?",
            (datetime.now().isoformat(timespec="seconds"), proposal_id),
        )
        conn.commit()
        conn.close()
        return {"ok": True, "proposal_id": proposal_id, "skill_name": row["skill_name"],
                "applied_to": str(md_path)}
    except Exception as e:
        conn.close()
        return {"ok": False, "error": f"应用失败: {e}"}


def skill_evolution_reject(proposal_id: int, db_path: Path = DEFAULT_DB) -> Dict:
    """驳回提案。"""
    _init_evolution_db(db_path)
    conn = _connect(db_path)
    conn.execute(
        "UPDATE skill_evolution_proposals SET status='rejected', updated_at=? WHERE id=?",
        (datetime.now().isoformat(timespec="seconds"), proposal_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "proposal_id": proposal_id, "status": "rejected"}


# ═══════════════════════════════════════════
# 4. 评测方法论 — 自建 QA 集打基线分 (对标 LoCoMo / PersonaMem)
# ═══════════════════════════════════════════

def _build_qa_set(db_path: Path) -> List[Dict]:
    """从 semantic_entries 构建自洽性 QA 集。

    对标 PersonaMem (记忆检索召回): 每条记忆取其前 60 字符作查询,
    目标是检索链路能否找回原文 (source_key)。按 source 分组,
    报告各层 (memory/experience/note/consolidated) 的独立分数。
    """
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT source_key, source, content FROM semantic_entries ORDER BY id"
    ).fetchall()
    conn.close()
    qa = []
    for r in rows:
        content = (r["content"] or "").strip()
        if len(content) < 20:
            continue
        # 查询 = 内容前 60 字符 (截断成"问题"), 目标 = source_key
        query = content[:60] + ("..." if len(content) > 60 else "")
        qa.append({"query": query, "target": r["source_key"], "source": r["source"]})
    return qa


def _eval_retrieval(qa: List[Dict], top_k: int, db_path: Path) -> Dict:
    """跑真实检索链路 (memory_search 语义通道), 计算 Recall@k / MRR。"""
    mem = CerebellumMemory(db_path)
    recall_at_1 = recall_at_k = 0
    mrr_sum = 0.0
    per_source: Dict[str, Dict] = {}
    n = len(qa)
    for item in qa:
        try:
            res = mem.search(item["query"], limit=top_k)
            hits = res.get("semantic_hits", []) or []
        except Exception:
            hits = []
        keys = [str(h.get("source_key")) for h in hits]
        rank = (keys.index(item["target"]) + 1) if item["target"] in keys else 0
        if rank == 1:
            recall_at_1 += 1
        if rank and rank <= top_k:
            recall_at_k += 1
        if rank:
            mrr_sum += 1.0 / rank
        src = item["source"]
        ps = per_source.setdefault(src, {"total": 0, "hit1": 0, "hitk": 0, "mrr": 0.0})
        ps["total"] += 1
        if rank == 1:
            ps["hit1"] += 1
        if rank and rank <= top_k:
            ps["hitk"] += 1
        if rank:
            ps["mrr"] += 1.0 / rank
    for src, ps in per_source.items():
        t = ps["total"]
        ps["recall@1"] = round(ps["hit1"] / t, 3) if t else 0.0
        ps["recall@k"] = round(ps["hitk"] / t, 3) if t else 0.0
        ps["mrr"] = round(ps["mrr"] / t, 3) if t else 0.0
    return {
        "queries": n,
        "top_k": top_k,
        "recall@1": round(recall_at_1 / n, 3) if n else 0.0,
        f"recall@{top_k}": round(recall_at_k / n, 3) if n else 0.0,
        "mrr": round(mrr_sum / n, 3) if n else 0.0,
        "per_source": per_source,
    }


def _render_report(metrics: Dict, db_path: Path) -> str:
    """生成 markdown 评测报告。"""
    lines = [
        "# 小脑记忆检索基线评测",
        "",
        f"- 评测时间: {datetime.now().isoformat(timespec='minutes')}",
        f"- 数据库: {db_path}",
        f"- 查询数: {metrics['queries']}  top_k: {metrics['top_k']}",
        "",
        "## 总体指标",
        "",
        "| 指标 | 分数 |",
        "|:-----|:-----|",
        f"| Recall@1 | {metrics['recall@1']} |",
        f"| Recall@{metrics['top_k']} | {metrics.get('recall@{0}'.format(metrics['top_k']), 0.0)} |",
        f"| MRR | {metrics['mrr']} |",
        "",
        "## 分层指标 (对标 PersonaMem 分域报告)",
        "",
        "| 层 | 查询数 | Recall@1 | Recall@k | MRR |",
        "|:---|:------|:---------|:---------|:----|",
    ]
    for src, ps in sorted(metrics.get("per_source", {}).items()):
        lines.append(
            f"| {src} | {ps['total']} | {ps['recall@1']} | "
            f"{ps['recall@k']} | {ps['mrr']} |"
        )
    lines.append("")
    lines.append("> 说明: 自洽性基线 (self-consistency), 查询取自记忆原文前缀。")
    lines.append("> 用于对比 Dreaming/反馈加权等改进前后分数, 分数提升 = 检索链路更稳。")
    return "\n".join(lines)


def benchmark_run(db_path: Path = DEFAULT_DB, top_k: int = BENCHMARK_TOP_K) -> Dict:
    """跑一轮检索基线评测 (对标 LoCoMo / PersonaMem 自建 QA 集)。

    返回指标 + 写入 benchmark_runs 表 + 生成 data/benchmark_report.md。
    """
    _init_evolution_db(db_path)
    qa = _build_qa_set(db_path)
    if not qa:
        return {"ok": False, "error": "semantic_entries 为空, 无法构建 QA 集 (先跑 index_vault/memory_save)"}
    metrics = _eval_retrieval(qa, top_k, db_path)
    report = _render_report(metrics, db_path)
    report_path = ""
    try:
        rp = Path(db_path).parent / "benchmark_report.md"
        rp.write_text(report, encoding="utf-8")
        report_path = str(rp)
    except Exception:
        pass
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO benchmark_runs (btype, metrics_json, report_path, created_at) VALUES (?, ?, ?, ?)",
        ("self_consistency", json.dumps(metrics, ensure_ascii=False), report_path,
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
    metrics["report_path"] = report_path
    return {"ok": True, "metrics": metrics}


def benchmark_list(db_path: Path = DEFAULT_DB, limit: int = 5) -> Dict:
    """查看评测历史 (对比基线)。"""
    _init_evolution_db(db_path)
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT id, btype, metrics_json, report_path, created_at "
        "FROM benchmark_runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        m = json.loads(r["metrics_json"])
        out.append({
            "id": r["id"], "btype": r["btype"], "created_at": r["created_at"],
            "recall@1": m.get("recall@1"), f"recall@{m.get('top_k', 5)}": m.get(f"recall@{m.get('top_k', 5)}"),
            "mrr": m.get("mrr"), "queries": m.get("queries"),
        })
    return {"ok": True, "runs": out}


def learning_loop_detect(db_path: Path = DEFAULT_DB, min_priority: int = 0,
                         limit: int = 50, persist: bool = False) -> Dict:
    """运行 10 模式学习循环检测器 (纯规则, 只读 cerebellum.db), 返回候选列表。

    候选字段为 camelCase (对齐 detector 输出契约), 见 learning_loop_detector.to_dict。
    persist=True 时把候选写入 learning_loop_candidates 表 (status=pending, 幂等去重)。
    """
    try:
        from learning_loop_detector import detect_all
    except ImportError:
        return {"ok": False, "error": "learning_loop_detector 模块不可用"}
    candidates = detect_all(db_path, min_priority=min_priority)
    if limit and len(candidates) > limit:
        candidates = candidates[:limit]
    if persist:
        saved = []
        for c in candidates:
            r = learning_loop_save(db_path, c)
            if r.get("ok"):
                saved.append(r["id"])
        return {"ok": True, "candidates": candidates, "persisted": saved,
                "count": len(candidates)}
    return {"ok": True, "candidates": candidates, "count": len(candidates)}


def learning_loop_save(db_path: Path = DEFAULT_DB, candidate: Dict = None) -> Dict:
    """持久化一条学习循环候选 (status=pending)。

    幂等: 同 pattern_id + observed_behavior 且 status=pending 时跳过重复插入。
    candidate 使用 detector 输出的 camelCase 字段名。
    """
    if not candidate:
        return {"ok": False, "error": "candidate 不能为空"}
    _init_evolution_db(db_path)
    conn = _connect(db_path)
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    pattern_id = candidate.get("patternId") or candidate.get("pattern_id", "")
    observed = candidate.get("observedBehavior") or candidate.get("observed_behavior", "")
    if not pattern_id or not observed:
        conn.close()
        return {"ok": False, "error": "candidate 缺少 patternId/observedBehavior"}
    dup = conn.execute(
        "SELECT id FROM learning_loop_candidates "
        "WHERE pattern_id=? AND observed_behavior=? AND status='pending'",
        (pattern_id, observed)).fetchone()
    if dup:
        conn.close()
        return {"ok": True, "id": dup["id"], "duplicate": True}
    conn.execute(
        "INSERT INTO learning_loop_candidates "
        "(pattern_id, claim_type, severity, title, observed_behavior, source_episodes, "
        " current_cost, broken_stage, recommended_owner, intervention, provenance, "
        " confidence, priority_score, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pattern_id,
         candidate.get("claimType") or candidate.get("claim_type", ""),
         candidate.get("severity", "Low"),
         candidate.get("title", ""),
         observed,
         json.dumps(candidate.get("sourceEpisodes") or candidate.get("source_episodes", []),
                    ensure_ascii=False),
         candidate.get("currentCost") or candidate.get("current_cost", ""),
         candidate.get("brokenStage") or candidate.get("broken_stage", ""),
         candidate.get("recommendedOwner") or candidate.get("recommended_owner", ""),
         candidate.get("intervention", ""),
         candidate.get("provenance", "deterministic-derived"),
         float(candidate.get("confidence", 0.5)),
         int(candidate.get("priorityScore") or candidate.get("priority_score", 0)),
         "pending", now, now))
    conn.commit()
    new_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.close()
    return {"ok": True, "id": new_id}


def learning_loop_list(db_path: Path = DEFAULT_DB, status: str = None,
                       limit: int = 50) -> Dict:
    """读取已保存的学习循环候选 (按 priority_score 降序)。"""
    _init_evolution_db(db_path)
    conn = _connect(db_path)
    if status:
        rows = conn.execute(
            "SELECT * FROM learning_loop_candidates WHERE status=? "
            "ORDER BY priority_score DESC LIMIT ?", (status, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM learning_loop_candidates "
            "ORDER BY priority_score DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    out = [dict(r) for r in rows]
    return {"ok": True, "candidates": out, "count": len(out)}


# ═══════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="cerebellum_evolution",
                                 description="DeepCode 小脑记忆进化引擎")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_dream = sub.add_parser("dreaming", help="离线记忆巩固 (Dreaming)")
    p_dream.add_argument("--kind", default="session", choices=["session", "experience"])
    p_dream.add_argument("--no-llm", action="store_true", help="跳过 LLM 合并 (规则兜底)")

    p_fb = sub.add_parser("feedback_add", help="写入反馈 (显式)")
    p_fb.add_argument("--target-type", required=True, choices=["semantic", "experience", "session", "memory"])
    p_fb.add_argument("--target-key", required=True)
    p_fb.add_argument("--rating", required=True, type=int, choices=[1, -1, -2])
    p_fb.add_argument("--source", default="explicit")
    p_fb.add_argument("--comment", default="")
    p_fbl = sub.add_parser("feedback_list", help="列出反馈")
    p_fbl.add_argument("--target-type", default=None)
    p_fbl.add_argument("--target-key", default=None)

    p_sig = sub.add_parser("skill_signal", help="采集 Skill 执行信号")
    p_sig.add_argument("--skill", required=True)
    p_sig.add_argument("--type", default="failure", choices=["failure", "success"])
    p_sig.add_argument("--context", default="")
    p_sig.add_argument("--error", default="")

    p_prop = sub.add_parser("skill_propose", help="生成 Skill 进化提案 (失败>=3)")
    p_prop.add_argument("--skill", required=True)
    p_prop.add_argument("--no-llm", action="store_true")

    p_list = sub.add_parser("skill_proposals", help="列出进化提案")
    p_list.add_argument("--status", default=None, choices=["pending", "applied", "rejected"])

    p_apply = sub.add_parser("skill_apply", help="应用提案到 SKILL.md (人工确认后)")
    p_apply.add_argument("--proposal-id", required=True, type=int)
    p_rej = sub.add_parser("skill_reject", help="驳回提案")
    p_rej.add_argument("--proposal-id", required=True, type=int)

    p_bm = sub.add_parser("benchmark", help="跑检索基线评测")
    p_bm.add_argument("--top-k", type=int, default=BENCHMARK_TOP_K)
    p_bml = sub.add_parser("benchmark_history", help="查看评测历史")
    p_ll_detect = sub.add_parser("loop_detect", help="运行学习循环检测器 (10 模式, 纯规则)")
    p_ll_detect.add_argument("--min-priority", type=int, default=0)
    p_ll_detect.add_argument("--limit", type=int, default=50)
    p_ll_detect.add_argument("--persist", action="store_true", help="检测后持久化候选")
    p_ll_save = sub.add_parser("loop_save", help="保存一条学习循环候选 (JSON)")
    p_ll_save.add_argument("--candidate", required=True, help="候选 JSON 字符串")
    p_ll_list = sub.add_parser("loop_list", help="查看已保存的学习循环候选")
    p_ll_list.add_argument("--status", default=None, help="pending/applied/rejected/dismissed")
    p_ll_list.add_argument("--limit", type=int, default=50)

    args = ap.parse_args()
    if args.cmd == "dreaming":
        print(json.dumps(dreaming_run(kind=args.kind, use_llm=not args.no_llm),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "feedback_add":
        print(json.dumps(feedback_add(args.target_type, args.target_key, args.rating,
                                      args.source, args.comment),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "feedback_list":
        print(json.dumps(feedback_list(args.target_type, args.target_key),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "skill_signal":
        print(json.dumps(skill_signal_add(args.skill, args.type, args.context, args.error),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "skill_propose":
        print(json.dumps(skill_evolution_propose(args.skill, use_llm=not args.no_llm),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "skill_proposals":
        print(json.dumps(skill_evolution_list(args.status),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "skill_apply":
        print(json.dumps(skill_evolution_apply(args.proposal_id),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "skill_reject":
        print(json.dumps(skill_evolution_reject(args.proposal_id),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "benchmark":
        print(json.dumps(benchmark_run(top_k=args.top_k), ensure_ascii=False, indent=2))
    elif args.cmd == "benchmark_history":
        print(json.dumps(benchmark_list(), ensure_ascii=False, indent=2))
    elif args.cmd == "loop_detect":
        print(json.dumps(learning_loop_detect(min_priority=args.min_priority,
                                              limit=args.limit, persist=args.persist),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "loop_save":
        candidate = json.loads(args.candidate)
        print(json.dumps(learning_loop_save(candidate), ensure_ascii=False, indent=2))
    elif args.cmd == "loop_list":
        print(json.dumps(learning_loop_list(status=args.status, limit=args.limit),
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
