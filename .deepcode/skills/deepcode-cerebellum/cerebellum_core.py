#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepCode 小脑统一记忆引擎 — 核心模块
═══════════════════════════════════════════
统一收编现有全部记忆后端 + 设置快照 + 语义检索 + 经验提取 + 会话摘要。

小脑模型分工 (Ollama):
  qwen2.5:3b       通用文本 (摘要/分类/提取, 零成本)
  deepseek-r1:1.5b 本地推理 (设置分析/经验关联)
  nomic-embed-text 向量嵌入 (语义检索)

架构: 大脑 (DeepSeek) → MCP → 本引擎 → Ollama / SQLite / 各记忆后端
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ═══════════════════════════════════════════
# 路径与配置
# ═══════════════════════════════════════════

SKILL_DIR = Path(__file__).parent
DATA_DIR = SKILL_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DEFAULT_DB = DATA_DIR / "cerebellum.db"
OLLAMA_HOST = os.environ.get("CEREBELLUM_OLLAMA_HOST", "http://127.0.0.1:11434")
# 主 LLM: phi4-mini 指令遵循好、中文流利、体积小; 可用 CEREBELLUM_LLM_MODEL 覆盖
LLM_MODEL = os.environ.get("CEREBELLUM_LLM_MODEL", "phi4-mini")
REASON_MODEL = os.environ.get("CEREBELLUM_REASON_MODEL", "deepseek-r1:1.5b")
EMBED_MODEL = os.environ.get("CEREBELLUM_EMBED_MODEL", "bge-m3")

PROJECT_ROOT = SKILL_DIR.parent.parent.parent  # F:/DEEPCODE
SETTINGS_FILES = [
    PROJECT_ROOT / ".deepcode" / "settings.json",          # 项目级
    Path.home() / ".deepcode" / "settings.json",           # 用户级
]

# 现有记忆后端路径 (对齐 memory_manager.py)
BACKEND_PATHS = {
    "ruflo": PROJECT_ROOT / "data" / "memory" / "memory.db",
    "claude": PROJECT_ROOT / ".claude" / "memory.db",
    "flow": PROJECT_ROOT / ".claude-flow" / "data" / "memory.json",
    "plan": PROJECT_ROOT,  # task_plan.md / progress.md / findings.md
}
KNOWLEDGE_DB = PROJECT_ROOT / ".deepcode" / "skills" / "deepcode-knowledge" / "data" / "knowledge.db"
VAULT_DIR = PROJECT_ROOT / ".deepcode" / "skills" / "deepcode-knowledge" / "data" / "vault" / "notes"
THREAD_DB = PROJECT_ROOT / "database.db"  # agent_threads

# 密钥指纹: 只保留前缀几字符用于变更识别 (用户选择完整存储, 指纹用于 diff 展示)
_SECRET_FIELDS = ("apikey", "api_key", "token", "secret", "password", "key")

# 快照保留策略: 每 scope 只保留最近 N 份 (防表无限膨胀)
SNAPSHOT_KEEP = 20


# ═══════════════════════════════════════════
# SQLite 初始化
# ═══════════════════════════════════════════

def _connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DEFAULT_DB) -> None:
    conn = _connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,              -- project / user
            source_path TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,      -- 完整快照 (含密钥, 按用户要求)
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_snap_source ON settings_snapshots(scope, created_at);

        CREATE TABLE IF NOT EXISTS experience_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT NOT NULL,
            lesson TEXT NOT NULL,
            tags TEXT DEFAULT '[]',
            embedding TEXT,                   -- JSON float list
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_exp_task ON experience_entries(task);

        CREATE TABLE IF NOT EXISTS session_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            summary TEXT NOT NULL,
            embedding TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sess_created ON session_summaries(created_at);

        CREATE TABLE IF NOT EXISTS semantic_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            source TEXT NOT NULL,             -- settings / experience / session / memory / note
            source_key TEXT,
            embedding TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(source, source_key)        -- 语义去重: 同源同键只保留最新
        );
        CREATE INDEX IF NOT EXISTS idx_sem_source ON semantic_entries(source);

        -- r1 设置语义分析
        CREATE TABLE IF NOT EXISTS settings_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            analysis TEXT NOT NULL,           -- r1 分析报告
            risk_level TEXT DEFAULT 'info',   -- low / medium / high
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_an_scope ON settings_analyses(scope, created_at);

        -- 经验关联图谱 (embedding 相似度建边 + LLM 关系类型)
        CREATE TABLE IF NOT EXISTS experience_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_exp_id INTEGER NOT NULL,
            to_exp_id INTEGER NOT NULL,
            similarity REAL NOT NULL,
            relation TEXT DEFAULT 'similar',   -- similar / depends / conflicts / contrasts
            created_at TEXT NOT NULL,
            UNIQUE(from_exp_id, to_exp_id)
        );
        CREATE INDEX IF NOT EXISTS idx_edges_from ON experience_edges(from_exp_id);
        """
    )
    # 兼容旧库: 补充 relation 列 (已存在则忽略)
    try:
        conn.execute("ALTER TABLE experience_edges ADD COLUMN relation TEXT DEFAULT 'similar'")
        conn.commit()
    except Exception:
        pass
    # 兼容旧库: semantic_entries 去重 (每组 (source, source_key) 保留最新一条) 后建唯一索引
    try:
        conn.execute(
            "DELETE FROM semantic_entries WHERE id NOT IN ("
            "SELECT MAX(id) FROM semantic_entries GROUP BY source, source_key)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_sem_unique "
            "ON semantic_entries(source, source_key)"
        )
        conn.commit()
    except Exception:
        pass
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════
# Ollama 通道 (小脑硬件层)
# ═══════════════════════════════════════════

def _ollama_post(path: str, payload: Dict, timeout: int = 120) -> Dict:
    req = urllib.request.Request(
        f"{OLLAMA_HOST}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ollama_embed(texts: List[str]) -> List[List[float]]:
    """nomic-embed-text 向量化 (支持批处理)"""
    try:
        resp = _ollama_post("/api/embed", {
            "model": EMBED_MODEL, "input": texts,
        })
        return resp.get("embeddings", [])
    except Exception:
        return [[] for _ in texts]


def ollama_generate(prompt: str, model: str = LLM_MODEL, system: str = "",
                    temperature: float = 0.3, max_tokens: int = 512,
                    timeout: int = 120, retries: int = 1,
                    enable_thinking: Optional[bool] = None) -> str:
    """本地模型生成 (默认 qwen3:4b, 零成本)

    enable_thinking: qwen3 等思考模型的控制开关; None=不控制, False=关闭思考
                     (摘要/分类任务建议 False, 避免思考过程吃掉 token 预算)
    retries: r1:1.5b 等小模型偶发超时/空响应, 重试 1 次提升成功率。
    """
    last_err = ""
    for attempt in range(retries + 1):
        try:
            options = {"temperature": temperature, "num_predict": max_tokens}
            payload = {
                "model": model, "prompt": prompt, "system": system,
                "stream": False, "options": options,
            }
            if enable_thinking is not None:
                # qwen3 顶层 think 参数 (options 内不生效)
                payload["think"] = enable_thinking
            resp = _ollama_post("/api/generate", payload, timeout=timeout)
            out = (resp.get("response") or "").strip()
            if not out and resp.get("thinking"):
                # qwen3 等 thinking 模型兜底: response 为空时取思考内容
                out = (resp.get("thinking") or "").strip()[-200:]
            if out:
                return _strip_thinking(out)
            last_err = "空响应"
        except Exception as e:
            last_err = str(e)
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    return f"[cerebellum:ollama 不可用: {last_err}]"


def _strip_thinking(text: str) -> str:
    """剥离模型输出中的思考痕迹。

    - deepseek-r1 系列: 输出含 ```thinking ...``` 或 ​``` 块
    - qwen3 思考模式: 可能把思考内容和最终回答混在一起
    只保留最后一段非思考正文。
    """
    import re
    # 去掉 ```thinking ...``` 代码块
    text = re.sub(r"```(?:thinking|reasoning)?\s*.*?```", "", text, flags=re.DOTALL)
    # 去掉 <thinking>...</thinking> 标签
    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL)
    # 去掉 "首先..." / "我需要..." 这类思考性开头 (r1 常见)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines:
        first = lines[0].strip()
        if re.match(r"^(首先|我需要|让我们|作为|用户要求|根据要求)", first):
            lines = lines[1:]
    return "\n".join(lines).strip()


def ollama_status() -> Dict:
    """小脑健康检查"""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
        models = [m["name"] for m in tags.get("models", [])]
        return {
            "ok": True, "host": OLLAMA_HOST, "models": models,
            "llm": LLM_MODEL, "reason": REASON_MODEL, "embed": EMBED_MODEL,
        }
    except Exception as e:
        return {"ok": False, "host": OLLAMA_HOST, "error": str(e)}


# ═══════════════════════════════════════════
# L0 设置层 — settings.json 快照 + 变更检测
# ═══════════════════════════════════════════

def _load_settings_file(path: Path) -> Optional[Dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _hash_content(obj: Dict) -> str:
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def _secret_fingerprint(value: str) -> str:
    """密钥指纹: sk-2ba21d...f66 — 保留首尾用于识别, 不暴露完整值"""
    if not value:
        return ""
    v = str(value)
    if len(v) <= 12:
        return "*" * len(v)
    return f"{v[:8]}...{v[-4:]}"


def _sanitize_for_diff(obj: Any, path: str = "") -> Any:
    """递归脱敏用于 diff 展示 (仅展示层, 快照本体完整存储)"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = k.lower()
            if isinstance(v, str) and any(f in kl for f in _SECRET_FIELDS) and len(v) > 6:
                out[k] = _secret_fingerprint(v)
            else:
                out[k] = _sanitize_for_diff(v, f"{path}.{k}")
        return out
    if isinstance(obj, list):
        return [_sanitize_for_diff(i, path) for i in obj]
    return obj


def settings_snapshot(scope: str = "all", db_path: Path = DEFAULT_DB) -> Dict:
    """快照 settings.json → settings_snapshots 表

    用户决策: 完整存储 (含密钥)。diff 展示时脱敏。
    """
    init_db(db_path)
    conn = _connect(db_path)
    results = []
    scopes = ["project", "user"] if scope == "all" else [scope]
    for sc in scopes:
        path = SETTINGS_FILES[0] if sc == "project" else SETTINGS_FILES[1]
        data = _load_settings_file(path)
        if data is None:
            results.append({"scope": sc, "status": "skipped", "reason": f"{path} 不存在"})
            continue
        h = _hash_content(data)
        # 查上一份快照比较变更
        prev = conn.execute(
            "SELECT snapshot_json FROM settings_snapshots WHERE scope=? "
            "ORDER BY id DESC LIMIT 1", (sc,)
        ).fetchone()
        changed = True
        diff_preview = "首次快照"
        if prev:
            prev_hash = _hash_content(json.loads(prev["snapshot_json"]))
            changed = prev_hash != h
            diff_preview = _diff_summary(
                json.loads(prev["snapshot_json"]), data) if changed else "无变更"
        conn.execute(
            "INSERT INTO settings_snapshots (scope, source_path, snapshot_json, content_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sc, str(path), json.dumps(data, ensure_ascii=False),
             h, datetime.now().isoformat(timespec="seconds")),
        )
        # 快照保留策略: 每 scope 只保留最近 SNAPSHOT_KEEP 份 (防表无限膨胀)
        conn.execute(
            "DELETE FROM settings_snapshots WHERE scope=? AND id NOT IN ("
            "SELECT id FROM settings_snapshots WHERE scope=? ORDER BY id DESC LIMIT ?)",
            (sc, sc, SNAPSHOT_KEEP),
        )
        conn.commit()
        results.append({
            "scope": sc, "status": "snapshotted", "source": str(path),
            "hash": h, "changed": changed, "diff": diff_preview,
            "size_bytes": len(json.dumps(data, ensure_ascii=False)),
        })
    conn.close()
    return {"ok": True, "snapshots": results}


def _diff_summary(old: Dict, new: Dict) -> str:
    """两个配置对象的差异摘要 (顶层 key 级别)"""
    old_keys = set(old.keys())
    new_keys = set(new.keys())
    added = new_keys - old_keys
    removed = old_keys - new_keys
    changed = []
    for k in new_keys & old_keys:
        if old[k] != new[k]:
            changed.append(k)
    parts = []
    if added:
        parts.append(f"+ {sorted(added)}")
    if removed:
        parts.append(f"- {sorted(removed)}")
    if changed:
        parts.append(f"~ {sorted(changed)}")
    return "; ".join(parts) if parts else "无顶层变更"


# ═══════════════════════════════════════════
# L0+ 设置语义分析 — deepseek-r1:1.5b (本地推理)
# ═══════════════════════════════════════════

ANALYSIS_SYSTEM = (
    "你是 DeepCode 小脑的设置分析师。基于 DEEPCODE settings.json 的配置摘要，"
    "输出三段分析: 1) 配置语义 2) 潜在风险 3) 优化建议。"
    "聚焦 DEEPCODE 特有内容 (provider/hooks/mcpServers/permissions/enabledSkills)，"
    "简短精炼，每段不超过 60 字。密钥不讨论。"
)


def settings_analyze(scope: str = "project", db_path: Path = DEFAULT_DB,
                     use_llm: bool = True) -> Dict:
    """用 deepseek-r1:1.5b 语义分析设置 — 配置语义/风险/建议

    同一 hash 只分析一次 (幂等), 结果持久化到 settings_analyses 表。
    """
    init_db(db_path)
    latest = settings_latest(scope, db_path)
    if not latest.get("ok"):
        return {"ok": False, "error": latest.get("error")}

    data = latest["settings"]
    h = latest["hash"]
    # 幂等: 已分析过同一 hash 则直接返回缓存
    conn = _connect(db_path)
    cached = conn.execute(
        "SELECT analysis, risk_level, created_at FROM settings_analyses "
        "WHERE scope=? AND content_hash=? ORDER BY id DESC LIMIT 1",
        (scope, h),
    ).fetchone()
    if cached:
        conn.close()
        return {"ok": True, "cached": True, "scope": scope, "hash": h,
                "analysis": cached["analysis"], "risk_level": cached["risk_level"],
                "created_at": cached["created_at"]}

    # 构造配置摘要 (脱敏 + 聚焦关键节)
    summary_lines = []
    red = latest["settings_redacted"]
    for section in ("provider", "hooks", "permissions", "enabledSkills"):
        if section in red:
            summary_lines.append(f"[{section}] {json.dumps(red[section], ensure_ascii=False)[:600]}")
    if "mcpServers" in red:
        servers = {k: {"command": v.get("command", "")[:60]}
                   for k, v in red["mcpServers"].items()}
        summary_lines.append(f"[mcpServers] {json.dumps(servers, ensure_ascii=False)[:600]}")
    summary = "\n".join(summary_lines)[:2500]

    analysis = ""
    risk_level = "info"
    if use_llm:
        analysis = ollama_generate(
            f"settings.json 配置摘要:\n{summary}\n\n请分析:", model=REASON_MODEL,
            system=ANALYSIS_SYSTEM, temperature=0.2, max_tokens=500,
            timeout=240, retries=1,
        )
        # 清洗: r1 小模型可能回显输入片段, 从最后一个配置节标记后截取
        if analysis.startswith("[provider]") or analysis.startswith("[mcpServers]"):
            for marker in ("[mcpServers]", "[enabledSkills]", "[permissions]", "[hooks]"):
                idx = analysis.find(marker)
                if idx > 0:
                    analysis = analysis[idx + len(marker):]
                    break
            analysis = analysis.strip()
        risk_level = _infer_risk(analysis)
    if not analysis or analysis.startswith("[cerebellum"):
        analysis = (f"[规则分析] provider={red.get('provider', {}).get('active', '?')}, "
                    f"mcp 服务器 {len(red.get('mcpServers', {}))} 个, "
                    f"permissions.defaultMode={red.get('permissions', {}).get('defaultMode', '?')}")
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO settings_analyses (scope, content_hash, analysis, risk_level, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (scope, h, analysis, risk_level, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "cached": False, "scope": scope, "hash": h,
            "analysis": analysis, "risk_level": risk_level}


def _infer_risk(analysis: str) -> str:
    """从分析文本推断风险级别 (关键词规则)"""
    low_words = ("无需", "正常", "合理", "无风险", "良好")
    high_words = ("风险", "泄露", "暴露", "明文", "危险", "不安全", "切勿")
    if any(w in analysis for w in high_words):
        return "high"
    if any(w in analysis for w in low_words):
        return "low"
    return "medium"


def settings_analyses_history(scope: str = "project", db_path: Path = DEFAULT_DB,
                              limit: int = 5) -> Dict:
    """查看设置分析历史"""
    init_db(db_path)
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT scope, content_hash, analysis, risk_level, created_at "
        "FROM settings_analyses WHERE scope=? ORDER BY id DESC LIMIT ?",
        (scope, limit),
    ).fetchall()
    conn.close()
    return {"ok": True, "scope": scope,
            "analyses": [{"hash": r["content_hash"], "risk": r["risk_level"],
                          "analysis": r["analysis"], "created_at": r["created_at"]}
                         for r in rows]}


def settings_latest(scope: str = "project", db_path: Path = DEFAULT_DB) -> Dict:
    """读取最新一份快照 (完整含密钥, 或展示脱敏版)"""
    init_db(db_path)
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT snapshot_json, created_at, content_hash FROM settings_snapshots "
        "WHERE scope=? ORDER BY id DESC LIMIT 1", (scope,)
    ).fetchone()
    conn.close()
    if not row:
        return {"ok": False, "error": f"{scope} 无快照, 请先 snapshot"}
    data = json.loads(row["snapshot_json"])
    return {
        "ok": True, "scope": scope, "created_at": row["created_at"],
        "hash": row["content_hash"],
        "settings": data,
        "settings_redacted": _sanitize_for_diff(data),
    }


def settings_search(query: str, db_path: Path = DEFAULT_DB) -> Dict:
    """在设置快照里按 key 名搜索 (语义优先, 关键词兜底)"""
    init_db(db_path)
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT scope, snapshot_json, created_at FROM settings_snapshots "
        "ORDER BY id DESC LIMIT 6"
    ).fetchall()
    conn.close()
    hits = []
    q = query.lower()
    for row in rows:
        data = json.loads(row["snapshot_json"])
        for k, v in _flatten(data):
            if q in k.lower() or (isinstance(v, str) and q in v.lower()):
                hits.append({
                    "scope": row["scope"], "key": k,
                    "value": _secret_fingerprint(v) if isinstance(v, str) and
                             any(f in k.lower() for f in _SECRET_FIELDS) else v,
                    "snapshot_at": row["created_at"],
                })
    # 用 embedding 语义补搜 (体验层: 语义查"模型"能找到 provider.model)
    if not hits:
        embed = ollama_embed([query])
        if embed and embed[0]:
            hits = _semantic_search_snapshots(query, embed[0], db_path)
    return {"ok": True, "query": query, "hits": hits[:20]}


def _flatten(obj: Any, prefix: str = "") -> List[tuple]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                out.extend(_flatten(v, p))
            else:
                out.append((p, v))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_flatten(v, f"{prefix}[{i}]"))
    return out


# P0-2 决策: 暂不引入 sqlite-vec ANN 向量索引 — 当前数据量小 (semantic_entries≈54,
# experience_entries≈12, settings_snapshots≈15), P0-1 修复后线性扫描 + 本地余弦 <10ms,
# 引入 ANN 扩展复杂度 > 收益。数据量达到万级时再评估。

# 设置快照语义检索的进程内 embedding 缓存 — settings_snapshots 无 embedding 列,
# 逐条重嵌成本高 (每次 50-200ms), 用缓存避免同一快照反复重嵌
_SNAP_EMBED_CACHE: Dict[tuple, List[float]] = {}


def _semantic_search_snapshots(query: str, q_embed: List[float],
                               db_path: Path, limit: int = 8) -> List[Dict]:
    """向量相似度检索设置快照 (小脑记忆: 记不清原词也能找到)
    轻量优化: 只扫最近 3 份快照 × 每份最多 40 个键值对, 命中向量走进程内缓存免重嵌"""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT id, scope, snapshot_json, created_at FROM settings_snapshots "
        "ORDER BY id DESC LIMIT 3"
    ).fetchall()
    conn.close()
    scored = []
    for row in rows:
        data = json.loads(row["snapshot_json"])
        pairs = [(k, v) for k, v in _flatten(data)
                 if isinstance(v, str) and len(v) >= 8][:40]
        for k, v in pairs:
            cache_key = (row["id"], k)
            cand_embed = _SNAP_EMBED_CACHE.get(cache_key)
            if cand_embed is None:
                cand_embed = ollama_embed([f"{k}: {v}"])[0]
                if cand_embed:
                    _SNAP_EMBED_CACHE[cache_key] = cand_embed
            if not cand_embed:
                continue
            sim = _cosine(q_embed, cand_embed)
            if sim > 0.5:
                scored.append({
                    "scope": row["scope"], "key": k, "value": v[:120],
                    "similarity": round(sim, 3), "snapshot_at": row["created_at"],
                })
    scored.sort(key=lambda x: -x["similarity"])
    return scored[:limit]


# ═══════════════════════════════════════════
# L1 事实层 — 统一记忆收编 (代理现有 5 后端)
# ═══════════════════════════════════════════

class CerebellumMemory:
    """统一记忆入口 — 收编 memory_manager 的 5 个后端 + 语义索引"""

    def __init__(self, db_path: Path = DEFAULT_DB):
        init_db(db_path)
        self.db_path = db_path
        self._mm = None
        try:
            sys.path.insert(0, str(PROJECT_ROOT / ".deepcode" / "skills" / "deepcode-knowledge"))
            from memory_manager import MemoryManager
            self._mm = MemoryManager()
        except Exception:
            self._mm = None

    def save(self, key: str, value: str, tags: Optional[List[str]] = None,
             backend: str = "auto") -> Dict:
        """写记忆 — 默认写全部可用后端, 并同步语义索引"""
        record = {
            "key": key, "value": value, "tags": tags or [],
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        written = []
        if self._mm is not None:
            try:
                ids = self._mm.save(key, value, backend=backend, tags=tags or [])
                written = ids if isinstance(ids, list) else [ids]
            except Exception as e:
                written = [f"err:{e}"]
        # 同步语义索引
        embed = ollama_embed([f"{key}: {value}"])[0]
        self._index_semantic(f"{key}: {value}", "memory", key, embed)
        return {"ok": True, "key": key, "backends": written, "indexed": bool(embed)}

    def load(self, key: str, backend: str = "auto") -> Dict:
        val = None
        src = None
        if self._mm is not None:
            try:
                val = self._mm.load(key, backend=backend)
                src = "unified-memory"
            except Exception:
                pass
        if val is None:
            val = self._load_from_semantic(key)
            src = "semantic"
        return {"ok": True, "key": key, "value": val, "source": src}

    def _load_from_semantic(self, key: str):
        conn = _connect(self.db_path)
        row = conn.execute(
            "SELECT content FROM semantic_entries WHERE source_key=? "
            "ORDER BY id DESC LIMIT 1", (key,)
        ).fetchone()
        conn.close()
        if row:
            content = row["content"]
            return content.split(": ", 1)[1] if ": " in content else content
        return None

    def search(self, query: str, backend: str = "auto", limit: int = 10) -> Dict:
        """语义优先 + 关键词兜底"""
        kw_hits = []
        if self._mm is not None:
            try:
                kw_hits = self._mm.search(query, backend=backend, limit=limit)
            except Exception:
                pass
        # 语义检索 semantic_entries
        q_embed = ollama_embed([query])[0]
        sem_hits = self._semantic_query(query, q_embed, limit) if q_embed else []
        return {
            "ok": True, "query": query,
            "keyword_hits": kw_hits, "semantic_hits": sem_hits,
        }

    def _semantic_query(self, query: str, q_embed: List[float], limit: int) -> List[Dict]:
        conn = _connect(self.db_path)
        rows = conn.execute(
            "SELECT content, source, source_key, embedding FROM semantic_entries "
            "ORDER BY id DESC LIMIT 200"
        ).fetchall()
        conn.close()
        scored = []
        for row in rows:
            cand_embed = _load_embedding(row["embedding"])
            if not cand_embed:
                continue
            sim = _cosine(q_embed, cand_embed)
            if sim > 0.45:
                scored.append({
                    "content": row["content"][:200], "source": row["source"],
                    "source_key": row["source_key"], "similarity": round(sim, 3),
                })
        scored.sort(key=lambda x: -x["similarity"])
        return scored[:limit]

    def list(self, backend: str = "auto") -> List[str]:
        if self._mm is not None:
            try:
                return self._mm.list(backend=backend)
            except Exception:
                pass
        return []

    def forget(self, key: str, backend: str = "auto") -> Dict:
        ok = False
        if self._mm is not None:
            try:
                ok = self._mm.forget(key, backend=backend)
            except Exception:
                pass
        conn = _connect(self.db_path)
        cur = conn.execute("DELETE FROM semantic_entries WHERE source_key=?", (key,))
        conn.commit()
        conn.close()
        return {"ok": True, "key": key, "removed": ok or cur.rowcount > 0}

    def stats(self) -> Dict:
        backend_stats = {}
        if self._mm is not None:
            try:
                backend_stats = self._mm.stats()
            except Exception:
                pass
        conn = _connect(self.db_path)
        counts = {
            "settings_snapshots": conn.execute("SELECT COUNT(*) FROM settings_snapshots").fetchone()[0],
            "experience_entries": conn.execute("SELECT COUNT(*) FROM experience_entries").fetchone()[0],
            "session_summaries": conn.execute("SELECT COUNT(*) FROM session_summaries").fetchone()[0],
            "semantic_entries": conn.execute("SELECT COUNT(*) FROM semantic_entries").fetchone()[0],
        }
        conn.close()
        return {"ok": True, "backends": backend_stats, "cerebellum": counts,
                "ollama": ollama_status()}

    def _index_semantic(self, content: str, source: str, source_key: str,
                        embed: List[float], db_path: Optional[Path] = None):
        conn = _connect(db_path or self.db_path)
        conn.execute(
            "INSERT OR REPLACE INTO semantic_entries "
            "(content, source, source_key, embedding, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (content, source, source_key,
             json.dumps(embed) if embed else None,
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        conn.close()


# ═══════════════════════════════════════════
# token-saver 语义缓存复用 (共享 token_saver_cache.db)
# 与 core/mcp_servers/token_saver_mcp_server.py 保持同算法:
#   精确 sha256 + difflib 近似 (必须 autojunk=False, 否则中文相似度暴跌)
# ═══════════════════════════════════════════

TOKEN_CACHE_DB = str(PROJECT_ROOT / "data" / "token_saver_cache.db")


def _cache_norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _cache_hash(text: str) -> str:
    return hashlib.sha256(_cache_norm(text).encode("utf-8")).hexdigest()


def _cache_similarity(a: str, b: str) -> float:
    import difflib
    a, b = _cache_norm(a), _cache_norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _cache_init(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache ("
        " hash TEXT PRIMARY KEY, key TEXT, value TEXT,"
        " created_at TEXT, hits INTEGER DEFAULT 0)"
    )


def _token_cache_lookup(key_text: str, sim_threshold: float = 0.82) -> Optional[str]:
    """查 token-saver 语义缓存 (精确 hash 优先 + 近似扫描), 命中返回缓存值。"""
    try:
        conn = sqlite3.connect(TOKEN_CACHE_DB, timeout=5)
        _cache_init(conn)
        h = _cache_hash(key_text)
        row = conn.execute("SELECT value FROM cache WHERE hash=?", (h,)).fetchone()
        if row:
            conn.execute("UPDATE cache SET hits=hits+1 WHERE hash=?", (h,))
            conn.commit()
            conn.close()
            return row[0]
        rows = conn.execute(
            "SELECT key, value FROM cache ORDER BY hits DESC LIMIT 50").fetchall()
        conn.close()
        best, best_sim = None, 0.0
        for k, v in rows:
            s = _cache_similarity(key_text, k)
            if s > best_sim:
                best, best_sim = v, s
        return best if best is not None and best_sim >= sim_threshold else None
    except Exception:
        return None  # 缓存故障不阻塞主流程


def _token_cache_store(key_text: str, value: str) -> None:
    """写 token-saver 语义缓存 (失败静默)。"""
    try:
        conn = sqlite3.connect(TOKEN_CACHE_DB, timeout=5)
        _cache_init(conn)
        h = _cache_hash(key_text)
        conn.execute(
            "INSERT OR REPLACE INTO cache (hash, key, value, created_at, hits)"
            " VALUES (?,?,?,?,COALESCE((SELECT hits FROM cache WHERE hash=?),0))",
            (h, _cache_norm(key_text)[:4000], value,
             datetime.now().isoformat(timespec="seconds"), h))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ═══════════════════════════════════════════
# L2 经验层 — PostTask 提炼 (qwen2.5:3b)
# ═══════════════════════════════════════════

EXPERIENCE_SYSTEM = (
    "你是一个经验提炼助手。下面给你一个「任务描述」，请从中提炼出"
    "可复用的经验教训。\n"
    "格式要求(必须严格遵守):\n"
    "教训1: ...\n"
    "教训2: ...\n"
    "教训3: ...\n"
    "每条教训一行、以「教训N:」开头、不超过40字、聚焦踩坑/配置/模式。\n"
    "禁止: 复述任务内容、解释过程、输出示例、添加任何其他文字。\n\n"
    "参考示例(仅示范格式，不要照抄内容):\n"
    "输入: 修复了 git push 被墙，配置代理后解决\n"
    "输出:\n"
    "教训1: 直连被墙时配置代理可解决\n"
    "教训2: git 失败优先查网络\n"
)


def experience_distill(task: str) -> str:
    """经验提炼: system 内嵌格式示例 + 低温度 + 确定性后处理。

    few-shot 示例放在 system(遵循度更高)，prompt 只给任务文本，避免
    模型把示例当对话上下文复述。后处理只保留「教训」开头的行。
    """
    lesson = ollama_generate(
        f"任务描述: {task[:1500]}",
        system=EXPERIENCE_SYSTEM,
        temperature=0.1,
        max_tokens=200, enable_thinking=False,
    )
    # 后处理: 只保留「教训」开头的行，丢弃模型可能输出的杂讯
    lines = [ln.strip() for ln in (lesson or "").splitlines()
             if ln.strip().startswith("教训")]
    return "\n".join(lines[:3]) if lines else lesson


def experience_record(task: str, db_path: Path = DEFAULT_DB,
                      use_llm: bool = True) -> Dict:
    """PostTask hook: 任务完成后用本地模型提炼经验"""
    init_db(db_path)
    lesson = ""
    cache_key = ""
    if use_llm:
        cache_key = f"[experience] {task[:1500]}"
        lesson = _token_cache_lookup(cache_key) or ""  # 语义缓存命中 → 免一次 Ollama 推理
    if not lesson and use_llm:
        lesson = experience_distill(task)
        if lesson and not lesson.startswith("[cerebellum") and any(
            ln.strip().startswith("教训") for ln in (lesson or "").splitlines()
        ):
            _token_cache_store(cache_key, lesson)  # 合格教训回填缓存
    # 质量闸门: 模型复述指令/输出杂讯时，丢弃并用规则兜底
    if not lesson or lesson.startswith("[cerebellum") or not any(
        ln.strip().startswith("教训") for ln in (lesson or "").splitlines()
    ):
        lesson = f"[规则提炼] {task[:200]}"
    embed = ollama_embed([f"{task[:100]}: {lesson}"])[0]
    conn = _connect(db_path)
    cur = conn.execute(
        "INSERT INTO experience_entries (task, lesson, tags, embedding, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (task[:500], lesson, json.dumps(["auto-extracted"]),
         json.dumps(embed) if embed else None,
         datetime.now().isoformat(timespec="seconds")),
    )
    exp_id = cur.lastrowid
    conn.commit()
    conn.close()
    # 同步语义索引 (source_key 用经验 id, 便于溯源和去重)
    mem = CerebellumMemory(db_path)
    mem._index_semantic(f"{task[:100]}: {lesson}", "experience", str(exp_id), embed)
    return {"ok": True, "task": task[:200], "lesson": lesson}


def experience_search(query: str, db_path: Path = DEFAULT_DB, limit: int = 5) -> Dict:
    init_db(db_path)
    q_embed = ollama_embed([query])[0]
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT task, lesson, created_at, embedding FROM experience_entries "
        "ORDER BY id DESC LIMIT 100"
    ).fetchall()
    conn.close()
    scored = []
    for row in rows:
        sim = 0.0
        if q_embed:
            cand = _load_embedding(row["embedding"])
            if cand:
                sim = _cosine(q_embed, cand)
        if sim > 0.4 or query.lower() in row["lesson"].lower():
            scored.append({
                "task": row["task"], "lesson": row["lesson"],
                "created_at": row["created_at"], "similarity": round(sim, 3),
            })
    scored.sort(key=lambda x: -x["similarity"])
    return {"ok": True, "query": query, "experiences": scored[:limit]}


# ═══════════════════════════════════════════
# L3 会话层 — PreCompact 摘要持久化
# ═══════════════════════════════════════════

SESSION_SYSTEM = (
    "你是 DeepCode 小脑的会话压缩器。把对话压缩为 200 字以内的结构化摘要, "
    "包含: 目标/已完成/关键决策/遗留事项。直接输出摘要，不要复述对话内容或解释过程。"
)


def session_summarize(context_text: str, session_id: str = "adhoc",
                      db_path: Path = DEFAULT_DB, use_llm: bool = True) -> Dict:
    """PreCompact hook: 压缩前用本地模型生成会话摘要并持久化"""
    init_db(db_path)
    summary = ""
    cache_key = ""
    if use_llm:
        cache_key = f"[session] {context_text[:4000]}"
        summary = _token_cache_lookup(cache_key) or ""  # 语义缓存命中 → 免一次 Ollama 推理
    if not summary and use_llm:
        summary = ollama_generate(
            f"对话内容:\n{context_text[:4000]}\n\n请压缩:", system=SESSION_SYSTEM,
            max_tokens=400, enable_thinking=False,
        )
        if summary and not summary.startswith("[cerebellum"):
            _token_cache_store(cache_key, summary)  # 合格摘要回填缓存
    if not summary or summary.startswith("[cerebellum"):
        summary = f"[规则摘要] 会话 {session_id} · {len(context_text)} 字符"
    embed = ollama_embed([summary])[0]
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO session_summaries (session_id, summary, embedding, created_at) "
        "VALUES (?, ?, ?, ?)",
        (session_id, summary, json.dumps(embed) if embed else None,
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "session_id": session_id, "summary": summary}


def session_recent(db_path: Path = DEFAULT_DB, limit: int = 5) -> Dict:
    init_db(db_path)
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT session_id, summary, created_at FROM session_summaries "
        "ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return {"ok": True, "sessions": [dict(r) for r in rows]}


# ═══════════════════════════════════════════
# L3+ 跨会话经验关联图谱 (embedding 相似度建边 + 关系类型)
# ═══════════════════════════════════════════


def classify_relations(candidates: List[tuple], use_llm: bool = True) -> List[str]:
    """判定候选经验对的关系类型。

    用确定性启发式(主题维度关键词)分类，稳定不依赖模型质量:
    - 同主题(编码/网络/安全/性能/配置/测试...) → similar
    - 不同主题但都有领域归属 → contrasts
    - 同主题且建议互相矛盾(不要X vs 必须X) → conflicts
    """
    return [_heuristic_relation((ri["lesson"] if ri else ""), (rj["lesson"] if rj else ""))
            for ri, rj, _sim in candidates]


# 主题维度 → 判定 similar 的依据
_RELATION_TOPICS = {
    "编码": ("编码", "encoding", "utf-8", "utf8", "gbk", "mbcs", "字符", "中文", "stdin", "stdout"),
    "网络": ("网络", "代理", "proxy", "墙", "连接", "端口", "curl"),
    "安全": ("安全", "密钥", "token", "凭据", "权限", "acl", "沙箱", "隔离", "sandbox"),
    "性能": ("性能", "慢", "超时", "内存", "缓存", "并发", "泄漏", "timeout"),
    "配置": ("配置", "settings", "环境变量", "config", "设置"),
    "测试": ("测试", "pytest", "test", "用例", "回归"),
    "提交": ("pr", "提交", "commit", "合并", "merge", "push", "分支"),
    "进程": ("进程", "spawn", "子进程", "job", "runner", "进程树"),
}


def _heuristic_relation(lesson_a: str, lesson_b: str) -> str:
    """确定性关系判定: 主题重叠→similar, 相反建议→conflicts, 不同主题→contrasts"""
    a = lesson_a.lower()
    b = lesson_b.lower()

    def topic_of(text: str) -> str | None:
        for topic, kws in _RELATION_TOPICS.items():
            if any(k in text for k in kws):
                return topic
        return None

    ta, tb = topic_of(a), topic_of(b)
    if ta and ta == tb:
        # 同主题: 检查是否互相矛盾 (仅真正的相反指令; "避免X问题" 不算否定)
        a_neg = any(k in a for k in ("不要", "禁止", "切勿", "别用", "避免使用", "avoid using", "never use"))
        b_neg = any(k in b for k in ("不要", "禁止", "切勿", "别用", "避免使用", "avoid using", "never use"))
        a_pos = any(k in a for k in ("必须", "务必", "一定要", "必须用", "must", "always"))
        b_pos = any(k in b for k in ("必须", "务必", "一定要", "必须用", "must", "always"))
        if (a_neg and b_pos) or (a_pos and b_neg):
            return "conflicts"
        return "similar"
    if ta and tb:
        return "contrasts"
    return "similar"
def experience_graph(db_path: Path = DEFAULT_DB, min_similarity: float = 0.5,
                      rebuild: bool = False) -> Dict:
    """构建经验关联图谱 — 向量相似度建边 + LLM 判定关系类型

    节点 = experience_entries, 边 = 语义相似度 >= min_similarity。
    每条边由 LLM 标注关系: similar/depends/conflicts/contrasts。
    rebuild=True 时重建全部边; 否则增量补边 (仅新增条目)。
    """
    init_db(db_path)
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT id, task, lesson, embedding FROM experience_entries ORDER BY id"
    ).fetchall()
    if not rows:
        conn.close()
        return {"ok": True, "nodes": 0, "edges": 0, "graph": {"nodes": [], "edges": []}}

    embeds = []
    for r in rows:
        try:
            embeds.append(json.loads(r["embedding"]) if r["embedding"] else [])
        except Exception:
            embeds.append([])

    if rebuild:
        conn.execute("DELETE FROM experience_edges")
        conn.commit()
        existing = set()
    else:
        existing = {tuple(x) for x in conn.execute(
            "SELECT from_exp_id, to_exp_id FROM experience_edges").fetchall()}

    # 收集候选边 (相似度达标且不存在)
    candidates = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if embeds[i] and embeds[j]:
                sim = _cosine(embeds[i], embeds[j])
                if sim >= min_similarity and (rows[i]["id"], rows[j]["id"]) not in existing:
                    candidates.append((rows[i], rows[j], round(sim, 3)))
    # LLM 判定关系类型 (similar/depends/conflicts/contrasts) — 批量分类
    relations = classify_relations(candidates, use_llm=True)
    new_edges = []
    for (ri, rj, sim), rel in zip(candidates, relations):
        new_edges.append((ri["id"], rj["id"], sim, rel))
    for f, t, sim, rel in new_edges:
        conn.execute(
            "INSERT OR IGNORE INTO experience_edges (from_exp_id, to_exp_id, similarity, relation, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (f, t, sim, rel, datetime.now().isoformat(timespec="seconds")),
        )
    conn.commit()

    # 组装图谱数据
    nodes = [{"id": r["id"], "task": r["task"][:60], "lesson": r["lesson"][:120]}
             for r in rows]
    edges = [{"from": r[0], "to": r[1], "similarity": r[2], "relation": r[3]}
             for r in conn.execute(
                 "SELECT from_exp_id, to_exp_id, similarity, relation FROM experience_edges").fetchall()]
    conn.close()
    return {"ok": True, "nodes": len(nodes), "edges": len(edges),
            "min_similarity": min_similarity, "graph": {"nodes": nodes, "edges": edges}}


def experience_graph_query(task_query: str, db_path: Path = DEFAULT_DB,
                           limit: int = 5) -> Dict:
    """按任务语义查询图谱 — 返回最相关的经验节点及其关联邻居"""
    init_db(db_path)
    q_embed = ollama_embed([task_query])[0]
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT id, task, lesson, embedding FROM experience_entries ORDER BY id"
    ).fetchall()
    conn.close()
    if not q_embed:
        return {"ok": True, "nodes": 0, "edges": 0, "graph": {"nodes": [], "edges": []}}

    scored = []
    for r in rows:
        try:
            emb = json.loads(r["embedding"]) if r["embedding"] else []
        except Exception:
            emb = []
        sim = _cosine(q_embed, emb) if emb else 0.0
        scored.append((sim, r))
    scored.sort(key=lambda x: -x[0])
    top = scored[:limit]

    # 收集命中节点及其图谱邻居 (去重)
    conn = _connect(db_path)
    node_ids = [r["id"] for _, r in top]
    neighbor_ids = set()
    edges = []
    for nid in node_ids:
        for row in conn.execute(
            "SELECT from_exp_id, to_exp_id, similarity, relation FROM experience_edges "
            "WHERE from_exp_id=? OR to_exp_id=?", (nid, nid)
        ):
            f, t, sim, rel = row
            edges.append({"from": f, "to": t, "similarity": sim, "relation": rel})
            neighbor_ids.update([f, t])
    nodes = [{"id": r["id"], "task": r["task"][:60], "lesson": r["lesson"][:120]}
             for _, r in top]
    for nid in neighbor_ids - set(node_ids):
        row = conn.execute(
            "SELECT id, task, lesson FROM experience_entries WHERE id=?", (nid,)
        ).fetchone()
        if row:
            nodes.append({"id": row["id"], "task": row["task"][:60],
                          "lesson": row["lesson"][:120]})
    conn.close()
    return {"ok": True, "nodes": len(nodes), "edges": len(edges),
            "graph": {"nodes": nodes, "edges": edges}}


# ═══════════════════════════════════════════
# L3.5 会话简报 — SessionStart 主动检索注入
# ═══════════════════════════════════════════

SESSION_BRIEFING_SYSTEM = (
    "你是 DeepCode 小脑的简报员。基于给定的历史经验列表，生成一份"
    "「前车之鉴」简报(150字以内): 概括最常见踩坑、最值得复用的模式、"
    "以及本次会话应该注意什么。直接输出简报正文，不要复述经验条目原文。"
)


def session_briefing(task_hint: str = "", db_path: Path = DEFAULT_DB,
                     limit: int = 5, use_llm: bool = True) -> Dict:
    """SessionStart: 检索最近/最相关经验，生成一份可注入的会话简报。

    让每次新会话"带着前车之鉴"开始——避免重复踩坑，优先复用已验证的模式。
    简报写入小脑 data/briefing.md，供会话上下文注入。
    """
    init_db(db_path)
    # 1. 检索: 有任务提示就语义检索，否则取最近经验
    experiences = []
    conn = _connect(db_path)
    if task_hint:
        try:
            q_embed = ollama_embed([task_hint])[0]
            rows = conn.execute(
                "SELECT id, task, lesson, embedding FROM experience_entries ORDER BY id DESC LIMIT 100"
            ).fetchall()
            scored = []
            for r in rows:
                try:
                    emb = json.loads(r["embedding"]) if r["embedding"] else []
                except Exception:
                    emb = []
                sim = _cosine(q_embed, emb) if (q_embed and emb) else 0.0
                scored.append((sim, r))
            scored.sort(key=lambda x: -x[0])
            experiences = [
                {"task": r["task"], "lesson": r["lesson"]}
                for _, r in scored[:limit]
            ]
        except Exception:
            experiences = []
    if not experiences:
        rows = conn.execute(
            "SELECT task, lesson FROM experience_entries ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        experiences = [{"task": r["task"], "lesson": r["lesson"]} for r in rows]
    conn.close()

    if not experiences:
        return {"ok": True, "briefing": "", "count": 0}

    # 2. LLM 生成简报
    briefing = ""
    if use_llm:
        exp_text = "\n".join(
            f"- {e['task'][:80]}: {e['lesson'][:150]}" for e in experiences
        )
        briefing = ollama_generate(
            f"历史经验:\n{exp_text}\n\n请生成前车之鉴简报:",
            system=SESSION_BRIEFING_SYSTEM,
            max_tokens=250, enable_thinking=False,
        )
    if not briefing or briefing.startswith("[cerebellum"):
        briefing = "\n".join(f"- {e['lesson'][:100]}" for e in experiences)

    # 3. 持久化到 data/briefing.md
    try:
        brief_path = Path(db_path).parent / "briefing.md"
        brief_path.write_text(
            f"<!-- 由 session_briefing 自动生成 {datetime.now().isoformat(timespec='minutes')} -->\n"
            f"{briefing}\n",
            encoding="utf-8",
        )
    except Exception:
        pass

    return {"ok": True, "briefing": briefing, "count": len(experiences)}


# ═══════════════════════════════════════════
# L4 知识层 — vault 索引 (供语义检索)
# ═══════════════════════════════════════════

def index_vault(db_path: Path = DEFAULT_DB) -> Dict:
    """把 knowledge vault 笔记索引进 semantic_entries, 支持语义检索"""
    init_db(db_path)
    if not VAULT_DIR.exists():
        return {"ok": False, "error": f"vault 不存在: {VAULT_DIR}"}
    indexed = 0
    mem = CerebellumMemory(db_path)
    for f in VAULT_DIR.glob("*.md"):
        content = f.read_text(encoding="utf-8", errors="replace")
        embed = ollama_embed([f"{f.stem}: {content[:500]}"])[0]
        mem._index_semantic(
            f"{f.stem}: {content[:500]}", "note", f.stem, embed)
        indexed += 1
    return {"ok": True, "indexed_notes": indexed, "vault": str(VAULT_DIR)}


# ═══════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════

def _load_embedding(raw: Optional[str]) -> List[float]:
    """安全解析数据库中存储的 embedding (JSON 文本) — 复用已存向量, 免重嵌"""
    if not raw:
        return []
    try:
        emb = json.loads(raw)
        return emb if isinstance(emb, list) and emb else []
    except Exception:
        return []


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def overview() -> Dict:
    """小脑全景状态"""
    return {
        "name": "deepcode-cerebellum",
        "ollama": ollama_status(),
        "db": str(DEFAULT_DB),
        "settings_files": [str(p) for p in SETTINGS_FILES],
        "memories": CerebellumMemory().stats(),
    }


# ═══════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "overview"
    if cmd == "overview":
        print(json.dumps(overview(), ensure_ascii=False, indent=2))
    elif cmd == "settings_snapshot":
        print(json.dumps(settings_snapshot(sys.argv[2] if len(sys.argv) > 2 else "all"),
                         ensure_ascii=False, indent=2))
    elif cmd == "settings_latest":
        print(json.dumps(settings_latest(sys.argv[2] if len(sys.argv) > 2 else "project"),
                         ensure_ascii=False, indent=2))
    elif cmd == "settings_search":
        print(json.dumps(settings_search(sys.argv[2]), ensure_ascii=False, indent=2))
    elif cmd == "experience_record":
        print(json.dumps(experience_record(sys.argv[2]), ensure_ascii=False, indent=2))
    elif cmd == "session_summarize":
        ctx = sys.argv[2] if len(sys.argv) > 2 else ""
        sid = sys.argv[3] if len(sys.argv) > 3 else "adhoc"
        print(json.dumps(session_summarize(ctx, sid), ensure_ascii=False, indent=2))
    elif cmd == "index_vault":
        print(json.dumps(index_vault(), ensure_ascii=False, indent=2))
    elif cmd == "memory_save":
        print(json.dumps(CerebellumMemory().save(sys.argv[2], sys.argv[3]),
                         ensure_ascii=False, indent=2))
    elif cmd == "memory_search":
        print(json.dumps(CerebellumMemory().search(sys.argv[2]),
                         ensure_ascii=False, indent=2))
    elif cmd == "settings_analyze":
        print(json.dumps(settings_analyze(
            sys.argv[2] if len(sys.argv) > 2 else "project"),
            ensure_ascii=False, indent=2))
    elif cmd == "settings_analyses_history":
        print(json.dumps(settings_analyses_history(
            sys.argv[2] if len(sys.argv) > 2 else "project"),
            ensure_ascii=False, indent=2))
    elif cmd == "experience_graph":
        print(json.dumps(experience_graph(
            min_similarity=float(sys.argv[2]) if len(sys.argv) > 2 else 0.5,
            rebuild="--rebuild" in sys.argv),
            ensure_ascii=False, indent=2))
    elif cmd == "experience_graph_query":
        print(json.dumps(experience_graph_query(sys.argv[2]),
                         ensure_ascii=False, indent=2))
    else:
        print(json.dumps(overview(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
