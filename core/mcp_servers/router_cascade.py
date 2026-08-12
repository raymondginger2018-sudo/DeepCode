# -*- coding: utf-8 -*-
"""
router_cascade — Router MCP 的「本地小脑级联 + 语义缓存」模块
================================================================

为 router-mcp 的 simple 路由提供两条省 token 通道（借鉴 GitHub 上
RouteLM / qca-cascade / llm-router 等开源项目的混合架构经验）:

  1. 语义缓存  : bge-m3 向量化 + 余弦相似度 ≥0.95 命中, 直接返回历史答案 (0 token)
  2. 本地级联  : qwen3:4b 本地先答 → 信任评估通过则零成本返回,
                 不通过则升级云端 DeepSeek Flash (TrustEvaluator 模式)

设计原则:
  - 自包含: 仅用标准库 + urllib, 不跨目录 import cerebellum 代码, 独立可运行
  - 共享数据层: CACHE_DB 默认直连 cerebellum.db (semantic_cache 表与 cerebellum
    的 semantic_entries 表互不冲突), 向量经 semantic_entries(source='router-cache')
    跨进程复用, 避免重复 embedding; 可用 ROUTER_CACHE_DB 覆盖回独立库
  - 降级优先: 任何 Ollama/SQLite 异常都不抛, 返回 None/False, 主路由走原有路径
  - 最小代码: 不做配置化, 常量 + 环境变量即可

用法 (在 router_mcp_server.py 中):
    import router_cascade as cascade
    hit = cascade.cache_lookup(query, "simple")
    local = cascade.cascade_answer(query)
"""

import json
import os
import re
import sqlite3
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# 共享 Ollama 接入层 (core/ollama_client.py) — 统一 4 模块的 HTTP 调用
try:
    from ..ollama_client import embed as _oc_embed
    from ..ollama_client import generate as _oc_generate
    from ..ollama_client import status as _oc_status
except ImportError:
    # 兼容以顶层模块导入 (sys.path 含 core/mcp_servers 时), 退化为按 core 包导入
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from core.ollama_client import embed as _oc_embed
    from core.ollama_client import generate as _oc_generate
    from core.ollama_client import status as _oc_status

# ────────────────────────────────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────────────────────────────────
OLLAMA_HOST = os.environ.get("ROUTER_OLLAMA_HOST", "http://127.0.0.1:11434")
LLM_MODEL = os.environ.get("ROUTER_LOCAL_MODEL", "qwen3:4b")        # 本地小脑
EMBED_MODEL = os.environ.get("ROUTER_EMBED_MODEL", "bge-m3")        # 向量模型
# 去水 critic 用 qwen2.5:3b: 实测 qwen3:4b 会把"精简"指令当任务分析输出 (done_reason=length),
# 而 qwen2.5:3b 非思考模型指令遵循好, 能真正剥离客套话 (实测 180 字 → 72 字)
DEWATER_MODEL = os.environ.get("ROUTER_DEWATER_MODEL", "qwen2.5:3b")
CEREBELLUM_DB = os.environ.get(
    "CEREBELLUM_DB",
    "F:/DEEPCODE/.deepcode/skills/deepcode-cerebellum/data/cerebellum.db",
)
# 直连小脑库 (semantic_cache 表与 cerebellum 的 semantic_entries 表互不冲突),
# 使向量经 semantic_entries 跨进程复用; 仍可用 ROUTER_CACHE_DB 覆盖回独立库。
CACHE_DB = os.environ.get("ROUTER_CACHE_DB", CEREBELLUM_DB)
SIM_THRESHOLD = float(os.environ.get("ROUTER_CACHE_THRESHOLD", "0.95"))  # 语义缓存命中阈值
CACHE_MAX_SCAN = int(os.environ.get("ROUTER_CACHE_SCAN", "200"))          # 向量扫描上限
EMBED_DIM = 1024  # bge-m3 维度 (用作 struct 打包长度校验)

# ── 预算感知联动 (与 deepcode-engine auto_compact 口径一致) ──────────
BUDGET_CONTEXT_WINDOW = int(os.environ.get("ROUTER_BUDGET_WINDOW", "128000"))
BUDGET_HIGH_WATERMARK = float(os.environ.get("ROUTER_BUDGET_HIGH_WM", "0.7"))
BUDGET_LOW_THRESHOLD = float(os.environ.get("ROUTER_BUDGET_LOW_THRESHOLD", "0.4"))
BUDGET_SESSION_DIRS = (
    os.path.expanduser("~/.deepcode/sessions"),
    os.path.expanduser("~/.deepcode/projects"),
    os.path.expanduser("~/.claude/sessions"),
    os.path.expanduser("~/AppData/Local/deepcode/sessions"),
)

# 本地小脑的 system prompt: 要求简洁直接回答, 不废话
LOCAL_SYSTEM = (
    "你是一个本地轻量助手。请直接、简洁地回答用户问题，"
    "不要客套话，不要复述问题，不要输出与问题无关的内容。"
    "不知道就说不知道。回答控制在 200 字以内。"
)

# 信任评估: 失败标记 (本地模型不可用/出错的痕迹)
FAIL_MARKERS = (
    "[cerebellum:ollama 不可用",
    "[cerebellum:",
    "ollama 不可用",
    "服务不可用",
    "连接失败",
    "Traceback",
)

# 运行时统计
_stats = {
    "cache_hits": 0,
    "cache_misses": 0,
    "cache_stored": 0,
    "local_answers": 0,      # 本地小脑信任通过次数
    "local_escalated": 0,    # 本地不通过 → 升级云端次数
    "local_failures": 0,     # Ollama 不可用/生成失败次数
}

# 进程内 embedding 缓存: 同一查询在本进程内不重复调 Ollama
_EMBED_CACHE: Dict[str, List[float]] = {}


# ────────────────────────────────────────────────────────────────────────
# 预算感知联动 (与 deepcode-engine auto_compact 的 watermark 口径一致)
# ────────────────────────────────────────────────────────────────────────
_watermark_cache = {"key": None, "watermark": 0.0, "ts": 0.0}


def _estimate_tokens(text: str) -> int:
    """与 auto_compact.estimate_tokens 一致的快速 Token 估算。

    中文按 2 token/字, 其他按 0.4 token/字符 (英文约 4 字符/token)。
    """
    if not text:
        return 0
    chinese = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - chinese
    return int(chinese * 2.0 + other * 0.4)


def _find_session_file() -> Optional[str]:
    """自动发现当前会话 JSONL 文件 (最近修改的 ≥1KB 会话文件)。"""
    for d in BUDGET_SESSION_DIRS:
        if not os.path.isdir(d):
            continue
        try:
            files = sorted(Path(d).rglob("*.jsonl"),
                           key=os.path.getmtime, reverse=True)
        except OSError:
            continue
        for f in files:
            try:
                if os.path.getsize(f) >= 1024:
                    return str(f)
            except OSError:
                continue
    return None


def session_watermark() -> float:
    """估算当前会话 token 水位 (0.0~1.0)。

    读会话 JSONL 的 content 与 tool_calls[].result, 累加 estimate_tokens,
    除以 context_window。文件未变化时走缓存, 避免每次请求都扫几 MB 会话。
    任何异常返回 0.0 (不中断主服务)。
    """
    sf = _find_session_file()
    if not sf:
        return 0.0
    try:
        st = os.stat(sf)
        key = (sf, st.st_mtime_ns, st.st_size)
        if _watermark_cache["key"] == key:
            return _watermark_cache["watermark"]
    except OSError:
        return 0.0

    total = 0
    try:
        with open(sf, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except (json.JSONDecodeError, KeyError):
                    continue
                content = msg.get("content", "")
                if isinstance(content, str):
                    total += _estimate_tokens(content)
                for tc in msg.get("tool_calls", []) or []:
                    result = tc.get("result", "")
                    if isinstance(result, str):
                        total += _estimate_tokens(result)
    except OSError:
        return 0.0

    wm = min(total / BUDGET_CONTEXT_WINDOW, 1.0)
    _watermark_cache["key"] = key
    _watermark_cache["watermark"] = wm
    _watermark_cache["ts"] = time.time()
    return wm


def budget_pressure() -> float:
    """预算压力 0.0~1.0: 水位 >0.7 后线性爬升, 1.0 = 窗口将满。"""
    wm = session_watermark()
    if wm <= BUDGET_HIGH_WATERMARK:
        return 0.0
    return min((wm - BUDGET_HIGH_WATERMARK) / (1.0 - BUDGET_HIGH_WATERMARK), 1.0)


def trust_threshold() -> float:
    """动态信任阈值: 默认 0.6, 预算压力大时降到 0.4 (更多本地回答放行省 token)。"""
    p = budget_pressure()
    return BUDGET_LOW_THRESHOLD + (0.6 - BUDGET_LOW_THRESHOLD) * (1.0 - p)


# ────────────────────────────────────────────────────────────────────────
# Ollama 基础调用 — 统一走共享 core/ollama_client.py (零第三方依赖)
# ────────────────────────────────────────────────────────────────────────
def ollama_status() -> Dict:
    """Ollama 健康检查 (模型列表)"""
    return _oc_status(OLLAMA_HOST)


def ollama_embed(text: str) -> Optional[List[float]]:
    """bge-m3 向量化单个文本; 失败返回 None (进程内 _EMBED_CACHE 去重)"""
    try:
        hit = _EMBED_CACHE.get(text)
        if hit is not None:
            return hit
        vec = _oc_embed(OLLAMA_HOST, EMBED_MODEL, [text], timeout=60)[0]
        if vec:
            _EMBED_CACHE[text] = vec
            return vec
    except Exception:
        pass
    return None


def ollama_generate(prompt: str, system: str = LOCAL_SYSTEM,
                    temperature: float = 0.3, max_tokens: int = 512,
                    model: str = LLM_MODEL, timeout: int = 120) -> Optional[str]:
    """本地模型生成 (默认 qwen3:4b, 可指定其他模型); 失败/空响应返回 None"""
    try:
        return _oc_generate(
            OLLAMA_HOST, model, prompt,
            system=system, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout, think=False,
        )
    except Exception:
        pass
    return None


# ────────────────────────────────────────────────────────────────────────
# 向量工具
# ────────────────────────────────────────────────────────────────────────
def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _pack_embedding(v: List[float]) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


def _unpack_embedding(b: bytes) -> List[float]:
    return list(struct.unpack(f"{len(b) // 4}f", b))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()


# ────────────────────────────────────────────────────────────────────────
# 语义缓存 (SQLite + bge-m3 向量)
# ────────────────────────────────────────────────────────────────────────
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(CACHE_DB, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS semantic_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hash TEXT UNIQUE NOT NULL,
        query TEXT NOT NULL,
        embedding BLOB,
        response TEXT NOT NULL,
        route TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        hits INTEGER DEFAULT 0,
        last_hit TEXT
    )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cache_hits ON semantic_cache(hits DESC)"
    )
    conn.commit()


def _reuse_embedding(conn: sqlite3.Connection, h: str) -> Optional[List[float]]:
    """从 cerebellum semantic_entries 复用已存向量 (跨进程去重, JSON 文本格式)。
    表不存在 / 向量缺失 / 维度不符时返回 None, 不影响主流程。
    """
    try:
        row = conn.execute(
            "SELECT embedding FROM semantic_entries"
            " WHERE source='router-cache' AND source_key=?",
            (h,)).fetchone()
        if row and row[0]:
            v = json.loads(row[0])
            if v and len(v) == EMBED_DIM:
                return v
    except Exception:
        pass
    return None


def cache_lookup(query: str, route: str = "simple") -> Optional[Dict[str, Any]]:
    """语义缓存查询: 先精确 hash, 再 bge-m3 余弦扫描 (≥SIM_THRESHOLD 命中)。
    失败一律返回 None, 不影响主流程。
    """
    try:
        conn = _connect()
        _init_db(conn)
        # 快速路径: 精确 hash
        h = _hash(query)
        row = conn.execute(
            "SELECT response FROM semantic_cache WHERE hash=? AND route=?",
            (h, route)).fetchone()
        if row:
            _touch(conn, h, route)
            conn.close()
            _stats["cache_hits"] += 1
            return {"hit": True, "match": "exact", "similarity": 1.0, "response": row[0]}
        # 向量路径: 扫描最近 CACHE_MAX_SCAN 条 (hits 优先)
        # 先复用 cerebellum semantic_entries 中已有向量 (跨进程去重, 避免重复调 Ollama)
        emb = _reuse_embedding(conn, h) or ollama_embed(query)
        if emb is None:
            conn.close()
            _stats["cache_misses"] += 1
            return None
        rows = conn.execute(
            "SELECT hash, embedding, response FROM semantic_cache"
            " WHERE route=? AND embedding IS NOT NULL"
            " ORDER BY hits DESC, id DESC LIMIT ?",
            (route, CACHE_MAX_SCAN)).fetchall()
        conn.close()
        best, best_sim = None, 0.0
        for rh, rb, rv in rows:
            try:
                s = _cosine(emb, _unpack_embedding(rb))
            except Exception:
                continue
            if s > best_sim:
                best, best_sim = rv, s
        if best is not None and best_sim >= SIM_THRESHOLD:
            _touch_conn(rh, route)
            _stats["cache_hits"] += 1
            return {"hit": True, "match": "semantic", "similarity": round(best_sim, 3),
                    "response": best}
        _stats["cache_misses"] += 1
        return None
    except Exception:
        return None


def _touch(conn: sqlite3.Connection, h: str, route: str) -> None:
    try:
        conn.execute(
            "UPDATE semantic_cache SET hits=hits+1, last_hit=? WHERE hash=? AND route=?",
            (_now(), h, route))
        conn.commit()
    except Exception:
        pass


def _touch_conn(h: str, route: str) -> None:
    try:
        conn = _connect()
        _touch(conn, h, route)
        conn.close()
    except Exception:
        pass


def _store_semantic_entry(conn: sqlite3.Connection, query: str,
                          emb: Optional[List[float]], response: str) -> None:
    """双写 cerebellum.semantic_entries (source='router-cache'), 向量以 JSON 文本存储,
    供 cerebellum 与其他进程复用; 表不存在时静默跳过。
    """
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS semantic_entries ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " content TEXT NOT NULL,"
            " source TEXT NOT NULL,"
            " source_key TEXT,"
            " embedding TEXT,"
            " created_at TEXT NOT NULL,"
            " UNIQUE(source, source_key))")
        conn.execute(
            "INSERT OR REPLACE INTO semantic_entries"
            " (content, source, source_key, embedding, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (_norm(query)[:500], "router-cache", _hash(query),
             json.dumps(emb) if emb else None, _now()))
    except Exception:
        pass


def cache_store(query: str, response: str, route: str = "simple") -> bool:
    """写入语义缓存 (带 bge-m3 向量); 失败静默返回 False。
    同时双写 cerebellum.semantic_entries (source='router-cache'),
    使向量可被 cerebellum 与其他进程复用, 避免重复 embedding。
    """
    try:
        if not response or not response.strip():
            return False
        emb = ollama_embed(query)
        conn = _connect()
        _init_db(conn)
        conn.execute(
            "INSERT OR REPLACE INTO semantic_cache"
            " (hash, query, embedding, response, route, created_at, hits)"
            " VALUES (?,?,?,?,?,?,COALESCE((SELECT hits FROM semantic_cache WHERE hash=? AND route=?),0))",
            (_hash(query), _norm(query)[:500],
             _pack_embedding(emb) if emb else None,
             response, route, _now(), _hash(query), route))
        _store_semantic_entry(conn, query, emb, response)
        conn.commit()
        conn.close()
        _stats["cache_stored"] += 1
        return True
    except Exception:
        return False


def cache_stats() -> Dict[str, Any]:
    """缓存统计 (供 router_status 展示)"""
    try:
        conn = _connect()
        _init_db(conn)
        n, total_hits = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(hits),0) FROM semantic_cache").fetchone()
        conn.close()
        return {"entries": n, "total_hits": total_hits}
    except Exception as e:
        return {"entries": 0, "total_hits": 0, "error": str(e)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ────────────────────────────────────────────────────────────────────────
# 信任评估 (TrustEvaluator — 结构启发式, 不依赖第二模型)
# ────────────────────────────────────────────────────────────────────────
def _similarity(a: str, b: str) -> float:
    """字符级相似度 (SequenceMatcher, autojunk=False 对中文鲁棒)"""
    import difflib
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _keyword_hit(query: str, response: str) -> float:
    """query 核心 2-gram 在 response 中的覆盖率 (0~1)。

    中文无词典分词, 用 2-gram 近似提取关键词:
      "今天上海天气怎么样" → 今天/天上/上海/海天/天气/气怎/怎么/么样
    任一 gram 出现在 response 即算命中 (只要回答覆盖了问题的一角
    就有相关性)。无 gram 可提取时返回 1.0 (不扣分)。
    """
    q = re.sub(r"\s+", "", query)
    grams = {q[i:i + 2] for i in range(len(q) - 1)
             if re.fullmatch(r"[\u4e00-\u9fff]", q[i]) and re.fullmatch(r"[\u4e00-\u9fff]", q[i + 1])}
    if not grams:
        return 1.0
    hits = sum(1 for g in grams if g in response)
    return hits / len(grams)


def _ends_well(response: str) -> bool:
    """结尾自然度: 以标点收尾 (而非半截话/客套开头)"""
    tail = response[-30:].strip()
    return tail.endswith(("。", "！", "？", ".", "!", "?", "…")) or not tail


def trust_evaluate(query: str, response: str, threshold: float = 0.6) -> Dict[str, Any]:
    """信任评估: 结构验证 + 关键词覆盖 + 结尾自然度。
    返回 {"trusted": bool, "score": 0~1, "reasons": [str]}
    threshold: 信任阈值 (默认 0.6; 预算压力大时由调用方动态调低)
    """
    r = response.strip()
    q = _norm(query)
    reasons: List[str] = []
    # ── 硬性否决 ──
    if not r:
        return {"trusted": False, "score": 0.0, "reasons": ["空响应"]}
    if len(r) < 12:
        return {"trusted": False, "score": 0.0, "reasons": ["回答过短(<12字符)"]}
    for m in FAIL_MARKERS:
        if m in r:
            return {"trusted": False, "score": 0.0, "reasons": [f"含失败标记: {m}"]}
    if _similarity(q, r) > 0.8:
        return {"trusted": False, "score": 0.0, "reasons": ["疑似复述问题(相似度>0.8)"]}
    # ── 软评分 (满分 1.0, ≥threshold 信任) ──
    score = 0.0
    if len(r) >= 15:
        score += 0.4
        reasons.append("有实质内容(≥15字符)")
    kw = _keyword_hit(q, r)
    if kw >= 0.25:
        score += 0.4
        reasons.append(f"关键词覆盖 {kw:.0%}")
    if _ends_well(r):
        score += 0.2
        reasons.append("结尾自然")
    return {"trusted": score >= threshold, "score": round(score, 2), "reasons": reasons}


# ────────────────────────────────────────────────────────────────────────
# 本地级联入口
# ────────────────────────────────────────────────────────────────────────
def cascade_answer(query: str, temperature: float = 0.3,
                   max_tokens: int = 512) -> Optional[Dict[str, Any]]:
    """本地小脑先答 + 信任评估。
    通过 → {"response", "score", "reasons", "model"} (零成本)
    不通过/不可用 → None (由调用方升级云端)
    """
    if not ollama_status().get("ok"):
        _stats["local_failures"] += 1
        return None
    resp = ollama_generate(query, temperature=temperature, max_tokens=max_tokens)
    if resp is None:
        _stats["local_failures"] += 1
        return None
    verdict = trust_evaluate(query, resp, threshold=trust_threshold())
    if verdict["trusted"]:
        _stats["local_answers"] += 1
        return {"response": resp, "score": verdict["score"],
                "reasons": verdict["reasons"], "model": LLM_MODEL}
    _stats["local_escalated"] += 1
    return None


def cascade_stats() -> Dict[str, Any]:
    """级联 + 缓存统计（含预算感知状态）"""
    stats = {**_stats, **cache_stats()}
    stats["budget"] = {
        "watermark": round(session_watermark(), 4),
        "pressure": round(budget_pressure(), 4),
        "threshold": round(trust_threshold(), 3),
    }
    return stats


# ────────────────────────────────────────────────────────────────────────
# 云端输出去水 (本地 critic 剥离客套话, 零成本)
# ────────────────────────────────────────────────────────────────────────
DEWATER_MAX_CHARS = int(os.environ.get("ROUTER_DEWATER_MAX_CHARS", "400"))


def dewater_response(text: str, temperature: float = 0.1,
                     max_tokens: int = 256) -> str:
    """云端输出去水: 本地 critic 模型剥离客套话/重复结论。
    仅处理超过阈值的长文本; 本地不可用/生成失败/结果劣化时一律返回原文。
    """
    if not text or len(text) < DEWATER_MAX_CHARS:
        return text
    if not ollama_status().get("ok"):
        return text
    system = (
        "你是文本精简助手。请去掉回复中的客套话、开场白（如'好的''当然可以'"
        "'很高兴为你服务'）、重复结论和冗余修饰，只保留实质信息。"
        "保持核心内容完整、语气专业。直接输出精简后的文本，不要解释。"
    )
    out = ollama_generate(text, system=system, temperature=temperature,
                          max_tokens=max_tokens, model=DEWATER_MODEL)
    if not out or len(out) < 10:
        return text
    # 防止模型发散: 精简后比原文还长很多 → 放弃本次去水
    if len(out) > len(text) * 1.5:
        return text
    return out


# ────────────────────────────────────────────────────────────────────────
# Prompt 压缩 (进云端前的长输入, 零依赖规则版, LLMLingua 风格)
# ────────────────────────────────────────────────────────────────────────
PROMPT_COMPRESS_MIN_CHARS = int(os.environ.get("ROUTER_PROMPT_COMPRESS_MIN", "2000"))
PROMPT_COMPRESS_HEAD_RATIO = float(os.environ.get("ROUTER_PROMPT_COMPRESS_HEAD", "0.4"))
PROMPT_COMPRESS_TAIL_CHARS = int(os.environ.get("ROUTER_PROMPT_COMPRESS_TAIL", "800"))


def compress_prompt(text: str) -> str:
    """进云端前的长输入压缩 (零依赖规则版)。

    仅处理超长文本: 保留开头(背景/上下文) + 结尾(任务指令),
    中间抽取含数字/百分比/金额/日期的关键句作为摘要。
    压缩是确定性的 (相同输入→相同输出), 不破坏 DeepSeek 前缀缓存命中。
    """
    if not text or len(text) <= PROMPT_COMPRESS_MIN_CHARS:
        return text
    head_len = int(len(text) * PROMPT_COMPRESS_HEAD_RATIO)
    tail = text[-PROMPT_COMPRESS_TAIL_CHARS:]
    middle = text[head_len:-PROMPT_COMPRESS_TAIL_CHARS] if len(text) > head_len + PROMPT_COMPRESS_TAIL_CHARS else ""
    key_sents = [s.strip() for s in re.split(r"(?<=[。！？!?；;])", middle)
                 if re.search(r"[0-9]|%|％|元|万|亿|年|月|日", s)]
    if key_sents:
        summary = "…[中间省略]…" + "…".join(key_sents[:6])
    else:
        summary = f"…[中间省略 {len(middle)} 字符]…"
    return text[:head_len] + summary + tail


if __name__ == "__main__":
    # 冒烟自测: python router_cascade.py
    print("Ollama:", ollama_status())
    print("Stats :", cascade_stats())
    t = trust_evaluate("今天上海天气怎么样", "今天上海晴转多云，气温 25~31℃。")
    print("Trust :", t)
