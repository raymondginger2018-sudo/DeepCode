# -*- coding: utf-8 -*-
"""
█▀█ █▀█ █▀█ █▀█ █▀▀ █▀▀ █▀▄▀█ █▀▀ █▀█
█▄█ █▀▄ █▄█ █▄█ █▄▄ █▄▄ █░▀░█ ██▄ █▄█

Token-Saver-MCP · 纯本地 Token 压缩引擎
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  在线轨(毫秒级·零 LLM)   语义缓存 / 工具输出裁剪 / 实体保护
  离线轨(后台·小模型)     分层摘要压缩 (Ollama + Qwen2.5-3B)

  降级设计:LLM 后端不可用时自动退化为纯规则模式,绝不报错。

  工具清单:
    health            → 后端/模型/缓存状态
    estimate_tokens   → 本地 token 估算
    extract_entities  → 实体提取(保护层)
    compress_context  → 分层压缩对话历史(核心)
    summarize         → 单段文本摘要
    trim_tool_output  → 工具输出裁剪(零延迟)
    cache_lookup      → 语义缓存查询
    cache_store       → 写入缓存
    cache_stats       → 缓存统计
    stats             → token 节省记账
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import os
import re
import json
import time
import hashlib
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

# Windows 控制台默认 GBK,统一 stdout/stderr 为 UTF-8,防中文/¥ 崩溃
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path

# 共享 Ollama 接入层 (core/ollama_client.py) — 统一小脑/router_cascade/token_saver/ollama-mcp 的 HTTP 调用
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # F:/DEEPCODE
from core.ollama_client import chat as _oc_chat
from core.ollama_client import status as _oc_status
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("token-saver-mcp")

# ════════════════════════════════════════════════════════════════
# 配置(env 可覆盖)
# ════════════════════════════════════════════════════════════════

OLLAMA_HOST = os.environ.get("TOKEN_SAVER_OLLAMA_HOST", "http://127.0.0.1:11434")
MODEL = os.environ.get("TOKEN_SAVER_MODEL", "qwen2.5:3b")
DB_PATH = os.environ.get("TOKEN_SAVER_DB", "F:/DEEPCODE/data/token_saver_mcp.db")
CACHE_DB = os.environ.get("TOKEN_SAVER_CACHE_DB", "F:/DEEPCODE/data/token_saver_cache.db")

# 云端深度摘要 (DeepSeek API, 可选) — 日常走本地 3b, deep=True 走云端高质量
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

# 压缩阈值(tokens)与分层
THRESHOLD_SKIP = int(os.environ.get("TOKEN_SAVER_SKIP", "2000"))   # 低于此值不压
THRESHOLD_L2 = int(os.environ.get("TOKEN_SAVER_L2", "6000"))       # 超过走 LLM 摘要
MAX_SUMMARY_TOKENS = int(os.environ.get("TOKEN_SAVER_MAX_OUT", "512"))
KEEP_RECENT_ROUNDS = int(os.environ.get("TOKEN_SAVER_KEEP_RECENT", "2"))

# 成本表(¥/百万 tokens,用于记账换算)
COST_IN = float(os.environ.get("TOKEN_SAVER_COST_IN", "1"))
COST_OUT = float(os.environ.get("TOKEN_SAVER_COST_OUT", "2"))

# 工具输出裁剪默认值
TRIM_HEAD_LINES = int(os.environ.get("TOKEN_SAVER_TRIM_HEAD", "30"))
TRIM_TAIL_LINES = int(os.environ.get("TOKEN_SAVER_TRIM_TAIL", "10"))
TRIM_MAX_ITEMS = int(os.environ.get("TOKEN_SAVER_TRIM_ITEMS", "20"))

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(os.path.dirname(CACHE_DB), exist_ok=True)

# ════════════════════════════════════════════════════════════════
# Token 估算(中英混合规则,无外部依赖)
# ════════════════════════════════════════════════════════════════

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")


def estimate_tokens(text: str) -> int:
    """估算文本 token 数:中文 ~1.7 字符/token,英文 ~4 字符/token。"""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    rest = len(text) - cjk
    return int(cjk / 1.7 + rest / 4.0) + 1


def count_effective_tokens(messages: List[Dict[str, str]]) -> int:
    return sum(estimate_tokens(m.get("content", "")) for m in messages)


# ════════════════════════════════════════════════════════════════
# 实体保护层(正则抽取,压缩时强制保留)
# ════════════════════════════════════════════════════════════════

_ENTITY_PATTERNS = [
    ("stock_code", r"\b\d{6}\.(?:SH|SZ|BJ|HK)\b"),
    ("stock_code6", r"(?<!\d)\d{6}(?!\d)"),
    ("file_path", r"[A-Za-z]:[\\/][^\s\"',;]+|\/[\w\-.]+(?:\/[\w\-.]+)+"),
    ("amount", r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:元|万|亿|%|％|USD|CNY|RMB)"),
    ("uuid", r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
    ("hash", r"\b[0-9a-f]{16,64}\b"),
    ("api_key", r"(?i)\b(sk|ghp|gho|ak|pk)[-_][a-z0-9]{12,}\b"),
]


def extract_entities(text: str, max_per_type: int = 20) -> Dict[str, List[str]]:
    """抽取实体。密钥类实体仅标记存在,不回显明文(防泄漏)。"""
    result: Dict[str, List[str]] = {}
    for name, pat in _ENTITY_PATTERNS:
        found = re.findall(pat, text, re.IGNORECASE)
        uniq = []
        for f in found:
            if isinstance(f, tuple):
                f = f[0]
            if f not in uniq:
                uniq.append(f)
            if len(uniq) >= max_per_type:
                break
        result[name] = uniq
    if result.get("api_key"):
        result["api_key"] = ["<redacted:%d>" % len(result["api_key"])]
    return result


def entities_to_guard_text(entities: Dict[str, List[str]], limit: int = 60) -> str:
    """把实体集转成摘要 prompt 里的强制保留清单。"""
    parts = []
    for name, items in entities.items():
        if not items or name == "api_key":
            continue
        if name == "amount":
            continue  # 金额/百分比太多,不进清单,靠摘要模型自然保留
        parts.append("%s: %s" % (name, ", ".join(items[:limit])))
    return "\n".join(parts)


def _enforce_entities(summary_text: str, entities: Dict[str, List[str]]) -> str:
    """实体后处理:摘要模型漏掉的代码/路径/UUID 等关键实体,强制补全,保证零丢失。"""
    missing = []
    for name in ("stock_code", "file_path", "uuid", "hash"):
        for item in entities.get(name, []):
            if item not in summary_text:
                missing.append(item)
    if missing:
        return summary_text + "\n[关键实体补全] " + ", ".join(missing)
    return summary_text


# ════════════════════════════════════════════════════════════════
# 规则压缩器(纯本地,零延迟,零损耗控制)
# ════════════════════════════════════════════════════════════════

_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)


def _truncate_json_text(obj: Any, max_items: int = TRIM_MAX_ITEMS,
                        max_str: int = 300) -> Any:
    """递归裁剪 JSON:数组截断、长字符串截断。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[k] = _truncate_json_text(v, max_items, max_str)
        return out
    if isinstance(obj, list):
        if len(obj) > max_items:
            head = [_truncate_json_text(x, max_items, max_str) for x in obj[:max_items]]
            return head + ["...<%d items omitted>" % (len(obj) - max_items)]
        return [_truncate_json_text(x, max_items, max_str) for x in obj]
    if isinstance(obj, str):
        if len(obj) > max_str * 2:
            return obj[:max_str] + "…<%d chars omitted>" % (len(obj) - max_str * 2)
        return obj
    return obj


def trim_tool_output(text: str, head_lines: int = TRIM_HEAD_LINES,
                     tail_lines: int = TRIM_TAIL_LINES,
                     keep_code: bool = True) -> Dict[str, Any]:
    """工具输出裁剪:
    1. JSON 文本 → 结构化截断(数组/长串),压缩率最高
    2. 代码块 → 保留(或按 keep_code 截断)
    3. 普通文本 → 头尾保留 + 中间省略
    """
    text = text.strip()
    if not text:
        return {"ok": True, "text": "", "original_chars": 0, "compressed_chars": 0,
                "ratio": 1.0, "mode": "empty"}

    original = len(text)

    # 1) JSON 检测
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            data = json.loads(text)
            out = _truncate_json_text(data)
            out_text = json.dumps(out, ensure_ascii=False, indent=None,
                                  separators=(",", ":"))
            return _ratio_result(out_text, original, "json")
        except (ValueError, TypeError):
            pass

    # 2) 代码块保护
    if keep_code:
        blocks = _CODE_BLOCK_RE.findall(text)
        if blocks and sum(len(b) for b in blocks) > original * 0.5:
            # 代码占比高 → 保留函数签名,压缩正文
            def _shorten_code(m: "re.Match[str]") -> str:
                body = m.group(0)
                lines = body.splitlines()
                if len(lines) > head_lines + tail_lines + 4:
                    keep = lines[:head_lines] + ["    # ...<%d lines omitted>" % (len(lines) - head_lines - tail_lines)] + lines[-tail_lines:]
                    return "\n".join(keep)
                return body
            out_text = _CODE_BLOCK_RE.sub(_shorten_code, text)
            return _ratio_result(out_text, original, "code")

    # 3) 普通文本头尾截断
    lines = text.splitlines()
    if len(lines) > head_lines + tail_lines + 4:
        kept = lines[:head_lines] + ["…<%d lines omitted>…" % (len(lines) - head_lines - tail_lines)] + lines[-tail_lines:]
        out_text = "\n".join(kept)
        return _ratio_result(out_text, original, "text")

    return _ratio_result(text, original, "none")


def _ratio_result(out_text: str, original: int, mode: str) -> Dict[str, Any]:
    new_len = len(out_text)
    ratio = 1.0 - (new_len / original if original else 0.0)
    return {"ok": True, "text": out_text, "original_chars": original,
            "compressed_chars": new_len, "ratio": round(max(0.0, ratio), 4),
            "mode": mode}


def dedupe_repeated(text: str) -> str:
    """删除连续重复行(工具结果重复输出的常见形态)。"""
    lines = text.splitlines()
    out = []
    prev = None
    dup = 0
    for ln in lines:
        if ln == prev:
            dup += 1
        else:
            if dup >= 3:
                out.append("…<%d duplicate lines>…" % dup)
            out.append(ln)
            prev, dup = ln, 0
    if dup >= 3:
        out.append("…<%d duplicate lines>…" % dup)
    return "\n".join(out)


# ════════════════════════════════════════════════════════════════
# LLM 后端(Ollama 通道,带降级)
# ════════════════════════════════════════════════════════════════

class LLMBackend:
    """Ollama /api/chat 封装。后端不可用 → backend_ok=False,调用方走规则降级。"""

    def __init__(self, host: str = OLLAMA_HOST, model: str = MODEL):
        self.host = host.rstrip("/")
        self.model = model
        self.backend_ok: Optional[bool] = None

    def ping(self) -> bool:
        """健康检查 (统一走共享 core/ollama_client.py)"""
        info = _oc_status(self.host)
        if info.get("ok"):
            self.backend_ok = True
            self.models = info.get("models", [])
            return True
        self.backend_ok = False
        self.models = []
        return False

    def model_ready(self) -> bool:
        if not self.backend_ok:
            self.ping()
        if not self.backend_ok:
            return False
        name = self.model.split(":")[0]
        return any(name in m for m in getattr(self, "models", []))

    def chat(self, system: str, user: str, max_tokens: int = MAX_SUMMARY_TOKENS,
             timeout: float = 180.0) -> Optional[Dict[str, Any]]:
        """调用本地模型 (统一走共享 core/ollama_client.py)。
        返回 {text, prompt_tokens, output_tokens} 或 None。
        """
        if not self.model_ready():
            return None
        try:
            data = _oc_chat(
                self.host, self.model,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.2, max_tokens=max_tokens,
                timeout=int(timeout), keep_alive="30m",
            )
            msg = data.get("message", {}).get("content", "").strip()
            if not msg:
                return None
            return {
                "text": msg,
                "prompt_tokens": data.get("prompt_eval_count", estimate_tokens(system + user)),
                "output_tokens": data.get("eval_count", estimate_tokens(msg)),
            }
        except Exception:
            return None


class DeepSeekBackend:
    """云端深度摘要后端 (DeepSeek chat/completions) — 质量高, 按需付费。

    双通道策略:
      日常 (deep=False) → 本地 qwen2.5:3b (零成本, 快)
      深度 (deep=True)  → 本后端 (高质量摘要, 按 token 计费)
    API Key 未配置时 available()=False, 自动回落本地。
    """

    def __init__(self, api_key: str = "", base_url: str = "",
                 model: str = ""):
        self.api_key = api_key or DEEPSEEK_API_KEY
        self.base_url = base_url or DEEPSEEK_BASE_URL
        self.model = model or DEEPSEEK_MODEL

    def available(self) -> bool:
        return bool(self.api_key)

    def chat(self, system: str, user: str, max_tokens: int = MAX_SUMMARY_TOKENS,
             timeout: float = 120.0) -> Optional[Dict[str, Any]]:
        """调用 DeepSeek API。返回 {text, prompt_tokens, output_tokens} 或 None。"""
        if not self.available():
            return None
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.2,
                "stream": False,
            }
            req = urllib.request.Request(
                f"{self.base_url.rstrip('/')}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            msg = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if not msg:
                return None
            usage = data.get("usage", {})
            return {
                "text": msg,
                "prompt_tokens": usage.get("prompt_tokens", estimate_tokens(system + user)),
                "output_tokens": usage.get("completion_tokens", estimate_tokens(msg)),
            }
        except Exception:
            return None


_backend = LLMBackend()
_remote = DeepSeekBackend()

# ════════════════════════════════════════════════════════════════
# SQLite 存储(缓存 + 记账)
# ════════════════════════════════════════════════════════════════

def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hash TEXT UNIQUE NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        created_at TEXT NOT NULL,
        hits INTEGER DEFAULT 0
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS accounting (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        op TEXT NOT NULL,
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        saved_tokens INTEGER DEFAULT 0,
        mode TEXT DEFAULT 'rule',
        detail TEXT DEFAULT ''
    )""")
    conn.commit()


def _record(op: str, saved: int, mode: str = "rule",
            inp: int = 0, out: int = 0, detail: str = "") -> None:
    try:
        conn = _connect(DB_PATH)
        _init_db(conn)
        conn.execute(
            "INSERT INTO accounting (ts, op, input_tokens, output_tokens, saved_tokens, mode, detail)"
            " VALUES (?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), op,
             inp, out, saved, mode, detail[:500]))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════
# 语义缓存(精确 hash + Jaccard 相似度近似)
# ════════════════════════════════════════════════════════════════

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _hash_key(text: str) -> str:
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()


def _similarity(a: str, b: str) -> float:
    """SequenceMatcher 相似度(对中文插入/改写鲁棒),用于近似匹配。
    注意: 必须 autojunk=False, 否则中文高频字符(的/，/。)被当 junk,
    重复文本的相似度会暴跌到 ~0.02。
    """
    import difflib
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def cache_lookup(key_text: str, sim_threshold: float = 0.82,
                 max_candidates: int = 50) -> Optional[Dict[str, Any]]:
    """查询缓存。先精确 hash,再做相似度扫描。命中即记账(真实节省)。"""
    try:
        conn = _connect(CACHE_DB)
        _init_db(conn)
        h = _hash_key(key_text)
        row = conn.execute("SELECT value FROM cache WHERE hash=?", (h,)).fetchone()
        if row:
            conn.execute("UPDATE cache SET hits=hits+1 WHERE hash=?", (h,))
            conn.commit()
            conn.close()
            saved = max(0, estimate_tokens(key_text) - estimate_tokens(row[0]))
            _record("cache_hit", saved, "cache", inp=estimate_tokens(key_text),
                    out=estimate_tokens(row[0]), detail="exact")
            return {"hit": True, "match": "exact", "value": row[0],
                    "saved_tokens": saved}
        # 近似扫描(仅在小库上做,避免全表扫)
        rows = conn.execute(
            "SELECT key, value FROM cache ORDER BY hits DESC LIMIT ?",
            (max_candidates,)).fetchall()
        conn.close()
        best, best_sim = None, 0.0
        for k, v in rows:
            s = _similarity(key_text, k)
            if s > best_sim:
                best, best_sim = v, s
        if best is not None and best_sim >= sim_threshold:
            saved = max(0, estimate_tokens(key_text) - estimate_tokens(best))
            _record("cache_hit", saved, "cache", inp=estimate_tokens(key_text),
                    out=estimate_tokens(best), detail=f"similar:{round(best_sim,3)}")
            return {"hit": True, "match": "similar", "similarity": round(best_sim, 3),
                    "value": best, "saved_tokens": saved}
        return None
    except Exception:
        return None


def cache_store(key_text: str, value: str) -> Dict[str, Any]:
    try:
        conn = _connect(CACHE_DB)
        _init_db(conn)
        h = _hash_key(key_text)
        conn.execute(
            "INSERT OR REPLACE INTO cache (hash, key, value, created_at, hits)"
            " VALUES (?,?,?,?,COALESCE((SELECT hits FROM cache WHERE hash=?),0))",
            (h, _norm(key_text)[:4000], value,
             datetime.now(timezone.utc).isoformat(timespec="seconds"), h))
        conn.commit()
        conn.close()
        return {"ok": True, "hash": h[:12], "stored_chars": len(value)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cache_stats() -> Dict[str, Any]:
    try:
        conn = _connect(CACHE_DB)
        _init_db(conn)
        n, total_hits = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(hits),0) FROM cache").fetchone()
        conn.close()
        return {"entries": n, "total_hits": total_hits}
    except Exception as e:
        return {"entries": 0, "total_hits": 0, "error": str(e)}


# ════════════════════════════════════════════════════════════════
# 分层压缩(核心)
# ════════════════════════════════════════════════════════════════

_SUMMARY_SYSTEM = (
    "你是 Token 压缩器,把用户提供的对话历史压缩为简洁摘要。"
    "规则:1) 保留所有关键事实、决定、参数、代码标识符、文件路径;"
    "2) 删除寒暄、重复、中间过程细节;3) 输出纯文本摘要,不要输出解释。"
)


def _parse_messages(raw: Any) -> List[Dict[str, str]]:
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list) and all(isinstance(m, dict) for m in parsed):
                    return [{"role": str(m.get("role", "user")),
                             "content": str(m.get("content", ""))} for m in parsed]
            except (ValueError, TypeError):
                pass
        return [{"role": "user", "content": raw}]
    if isinstance(raw, list):
        return [{"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
                for m in raw if isinstance(m, dict)]
    return [{"role": "user", "content": str(raw)}]


def _rule_compress_message(content: str) -> str:
    """消息级规则压缩:工具输出裁剪 + 重复行删除。"""
    out = trim_tool_output(content, head_lines=TRIM_HEAD_LINES,
                           tail_lines=TRIM_TAIL_LINES, keep_code=True)
    if out["ratio"] > 0.05:
        return out["text"]
    return dedupe_repeated(content)


def compress_context(raw: Any, keep_recent: int = KEEP_RECENT_ROUNDS,
                     force_level: Optional[int] = None,
                     deep: bool = False) -> Dict[str, Any]:
    """分层压缩对话历史。

    层级:
      skip — 低于阈值,原文返回
      L1   — 规则裁剪(工具输出/重复),零延迟
      L2/L3 — LLM 摘要(带实体保护),后端不可用则退回规则
    双通道: deep=False 本地(零成本); deep=True 云端 DeepSeek(高质量)。
    """
    messages = _parse_messages(raw)
    if not messages:
        return {"ok": False, "error": "empty input"}

    total_tokens = count_effective_tokens(messages)
    if total_tokens < THRESHOLD_SKIP and force_level is None:
        return {"ok": True, "text": raw if isinstance(raw, str) else json.dumps(messages, ensure_ascii=False),
                "original_tokens": total_tokens, "compressed_tokens": total_tokens,
                "saved_tokens": 0, "ratio": 0.0, "level": "skip", "mode": "none"}

    # 实体保护:从全部内容抽取,摘要时强制保留
    full_text = "\n".join(m.get("content", "") for m in messages)
    entities = extract_entities(full_text)
    guard = entities_to_guard_text(entities)

    # 分层:最近 keep_recent 轮保留原文,其余压缩
    n = len(messages)
    recent = messages[n - keep_recent * 2:] if n > keep_recent * 2 else messages
    older = messages[:n - keep_recent * 2] if n > keep_recent * 2 else []

    recent_text = "\n".join("[%s] %s" % (m["role"], m["content"]) for m in recent)

    if not older:
        # 全部是最近轮次 → 只做规则裁剪
        parts = [m["content"] for m in recent]
        compressed = "\n".join(_rule_compress_message(p) for p in parts)
        mode, level = "rule", "L1"
    else:
        older_text = "\n".join("[%s] %s" % (m["role"], m["content"]) for m in older)
        # 先规则裁剪老消息(工具输出大 JSON 用规则,省 LLM 负担)
        older_ruled = "\n".join(_rule_compress_message(m["content"]) for m in older)

        # 缓存闭环: 相同老历史直接命中压缩摘要 (免 LLM)
        cache_hit = cache_lookup(older_text) if len(older_text) >= 200 else None
        if cache_hit:
            compressed = cache_hit["value"]
            mode, level = "cache", "L2"
        else:
            summary = None
            mode = "llm"
            # 双通道: deep → 云端优先; 日常 → 本地
            if deep and _remote.available():
                summary = _remote.chat(
                    system=_SUMMARY_SYSTEM + ("\n必须保留的实体:\n" + guard if guard else ""),
                    user="压缩以下对话历史(保留关键事实/决定/数字/代码):\n\n" + older_ruled[:20000],
                    max_tokens=MAX_SUMMARY_TOKENS)
                mode = "deep"
            if summary is None and _backend.model_ready() and (force_level is None or force_level >= 2):
                summary = _backend.chat(
                    system=_SUMMARY_SYSTEM + ("\n必须保留的实体:\n" + guard if guard else ""),
                    user="压缩以下对话历史(保留关键事实/决定/数字/代码):\n\n" + older_ruled[:20000],
                    max_tokens=MAX_SUMMARY_TOKENS)
            if summary:
                compressed = "[摘要] " + _enforce_entities(summary["text"], entities)
                level = "L2" if total_tokens < 12000 else "L3"
                # 自动回填缓存 (下次同历史直接命中)
                cache_store(older_text, compressed)
            else:
                compressed = older_ruled
                mode, level = "rule", "L1"

    out_text = compressed + "\n\n--- 最近对话(原文) ---\n" + recent_text
    out_tokens = estimate_tokens(out_text)
    saved = max(0, total_tokens - out_tokens)

    _record("compress_context", saved, mode, total_tokens, out_tokens,
            "level=%s entities=%d" % (level, sum(len(v) for v in entities.values())))

    return {"ok": True, "text": out_text,
            "original_tokens": total_tokens, "compressed_tokens": out_tokens,
            "saved_tokens": saved, "ratio": round(saved / total_tokens, 4) if total_tokens else 0,
            "level": level, "mode": mode, "entities_guarded": sum(len(v) for v in entities.values())}


# ════════════════════════════════════════════════════════════════
# FastMCP 工具层
# ════════════════════════════════════════════════════════════════

@mcp.tool()
def health() -> Dict[str, Any]:
    """查看后端/模型/缓存状态(含云端双通道)。"""
    _backend.ping()
    return {
        "ollama": _backend.host,
        "backend_ok": _backend.backend_ok,
        "model": _backend.model,
        "model_ready": _backend.model_ready(),
        "installed_models": getattr(_backend, "models", []),
        "remote": {"available": _remote.available(),
                   "model": _remote.model if _remote.available() else "",
                   "base_url": _remote.base_url if _remote.available() else ""},
        "cache": cache_stats(),
        "thresholds": {"skip": THRESHOLD_SKIP, "l2": THRESHOLD_L2,
                       "keep_recent": KEEP_RECENT_ROUNDS},
    }


@mcp.tool()
def estimate_tokens_tool(text: str) -> Dict[str, Any]:
    """估算文本 token 数(本地规则,无外部依赖)。"""
    n = estimate_tokens(text)
    return {"text_chars": len(text), "estimated_tokens": n,
            "cjk_chars": len(_CJK_RE.findall(text))}


@mcp.tool()
def extract_entities_tool(text: str) -> Dict[str, Any]:
    """提取保护实体:股票代码/文件路径/数字/UUID/哈希等。密钥类脱敏。"""
    ent = extract_entities(text)
    return {"entities": ent, "total": sum(len(v) for v in ent.values())}


@mcp.tool()
def trim_tool_output_tool(text: str, head_lines: int = TRIM_HEAD_LINES,
                          tail_lines: int = TRIM_TAIL_LINES,
                          keep_code: bool = True) -> Dict[str, Any]:
    """裁剪工具输出:JSON 结构化截断/文本头尾保留/代码块保护。零延迟。"""
    res = trim_tool_output(text, head_lines, tail_lines, keep_code)
    saved_tokens = estimate_tokens(text) - estimate_tokens(res["text"])
    _record("trim_tool_output", max(0, saved_tokens), res["mode"])
    return res


@mcp.tool()
def summarize(text: str, max_tokens: int = MAX_SUMMARY_TOKENS,
              deep: bool = False) -> Dict[str, Any]:
    """单段文本摘要。LLM 不可用时返回规则截断。

    双通道: deep=False 走本地模型(零成本); deep=True 走云端 DeepSeek(高质量,按量计费)。
    缓存闭环: 相同/近似文本先查缓存命中直接返回(免 LLM),
    LLM 摘要成功后自动回填缓存, 下次命中即省钱。
    """
    # 0. 缓存优先 (语义缓存闭环, 两通道共享缓存)
    if len(text) >= 200:  # 太短不查缓存 (收益低)
        hit = cache_lookup(text)
        if hit:
            saved = hit.get("saved_tokens", 0)
            _record("summarize", saved, "cache",
                    inp=estimate_tokens(text), out=estimate_tokens(hit["value"]),
                    detail=f"cache_{hit.get('match')}")
            return {"ok": True, "text": hit["value"], "mode": "cache",
                    "match": hit.get("match"),
                    "saved_tokens": saved}
    entities = extract_entities(text)
    guard = entities_to_guard_text(entities)

    # 后端选择: deep → 云端优先; 日常 → 本地
    res = None
    mode = "llm"
    if deep and _remote.available():
        res = _remote.chat(system=_SUMMARY_SYSTEM + ("\n必须保留实体:\n" + guard if guard else ""),
                           user=text[:20000], max_tokens=max_tokens)
        mode = "deep"
    if res is None:
        if not _backend.model_ready():
            out = trim_tool_output(text)
            _record("summarize", estimate_tokens(text) - estimate_tokens(out["text"]), "rule")
            return {"ok": True, "text": out["text"], "mode": "rule-fallback",
                    "note": "本地模型未就绪,已用规则截断"}
        res = _backend.chat(system=_SUMMARY_SYSTEM + ("\n必须保留实体:\n" + guard if guard else ""),
                            user=text[:20000], max_tokens=max_tokens)
    if not res:
        out = trim_tool_output(text)
        _record("summarize", estimate_tokens(text) - estimate_tokens(out["text"]), "rule")
        return {"ok": True, "text": out["text"], "mode": "rule-fallback"}
    saved = estimate_tokens(text) - estimate_tokens(res["text"])
    _record("summarize", max(0, saved), mode, res["prompt_tokens"], res["output_tokens"])
    final_text = _enforce_entities(res["text"], entities)
    # 1. 自动回填缓存 (下次同文/近似直接命中)
    if len(text) >= 200:
        cache_store(text, final_text)
    return {"ok": True, "text": final_text, "mode": mode,
            "saved_tokens": max(0, saved)}


@mcp.tool()
def compress_context_tool(raw: Any, keep_recent: int = KEEP_RECENT_ROUNDS,
                          force_level: Optional[int] = None,
                          deep: bool = False) -> Dict[str, Any]:
    """分层压缩对话历史(核心工具)。
    raw: 消息列表 JSON 或纯文本。
    force_level: 1=仅规则, 2+=强制 LLM 摘要。
    deep: True 走云端 DeepSeek(高质量,按量计费); False 走本地(零成本)。
    """
    return compress_context(raw, keep_recent, force_level, deep=deep)


@mcp.tool()
def cache_lookup_tool(key_text: str, sim_threshold: float = 0.82) -> Dict[str, Any]:
    """语义缓存查询:精确 hash + 相似度近似。命中返回缓存值。"""
    hit = cache_lookup(key_text, sim_threshold)
    if hit:
        return hit
    return {"hit": False}


@mcp.tool()
def cache_store_tool(key_text: str, value: str) -> Dict[str, Any]:
    """写入语义缓存。"""
    return cache_store(key_text, value)


@mcp.tool()
def cache_stats_tool() -> Dict[str, Any]:
    """缓存统计:条目数/命中次数。"""
    return cache_stats()


@mcp.tool()
def stats() -> Dict[str, Any]:
    """Token 节省记账(科学版): 实际消耗/节省/节省率/按日统计。"""
    try:
        conn = _connect(DB_PATH)
        _init_db(conn)
        total_saved, total_ops = conn.execute(
            "SELECT COALESCE(SUM(saved_tokens),0), COUNT(*) FROM accounting").fetchone()
        total_in, total_out = conn.execute(
            "SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) "
            "FROM accounting").fetchone()
        by_mode = conn.execute(
            "SELECT mode, COUNT(*), COALESCE(SUM(saved_tokens),0)"
            " FROM accounting GROUP BY mode").fetchall()
        by_op = conn.execute(
            "SELECT op, COUNT(*), COALESCE(SUM(saved_tokens),0)"
            " FROM accounting GROUP BY op ORDER BY 3 DESC").fetchall()
        by_day = conn.execute(
            "SELECT substr(ts,1,10) AS day, COUNT(*), "
            "COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
            "COALESCE(SUM(saved_tokens),0) "
            "FROM accounting GROUP BY day ORDER BY day DESC LIMIT 14").fetchall()
        conn.close()
        total_consumed = total_in + total_out
        original_total = total_consumed + total_saved
        save_rate = round(total_saved / original_total * 100, 1) if original_total else 0.0
        cost_saved = total_saved / 1e6 * COST_IN
        return {
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "total_consumed_tokens": total_consumed,
            "total_saved_tokens": total_saved,
            "original_tokens_without_saver": original_total,
            "save_rate_pct": save_rate,
            "total_ops": total_ops,
            "estimated_cost_saved_yuan": round(cost_saved, 4),
            "cost_rate": "¥%s/M input, ¥%s/M output" % (COST_IN, COST_OUT),
            "by_mode": [{"mode": m, "ops": c, "saved_tokens": s} for m, c, s in by_mode],
            "by_op": [{"op": o, "ops": c, "saved_tokens": s} for o, c, s in by_op],
            "by_day": [{"day": d, "ops": c, "input": i, "output": o, "saved": s}
                       for d, c, i, o, s in by_day]}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def record_llm_call(input_tokens: int = 0, output_tokens: int = 0,
                    saved_tokens: int = 0, mode: str = "llm",
                    op: str = "call", detail: str = "") -> Dict[str, Any]:
    """全量记账: 记录一次 LLM 调用消耗。供外部每次调用前后调用。

    input_tokens: 本次调用输入 tokens (含 history)
    output_tokens: 本次调用输出 tokens
    saved_tokens: 本次因缓存/压缩节省的 tokens (0 表示未节省)
    mode: llm / cache / rule / local / cloud
    op: call / compress / cache_hit / summarize
    返回累计统计, 便于外部展示。
    """
    if input_tokens < 0 or output_tokens < 0 or saved_tokens < 0:
        return {"error": "tokens 不能为负"}
    _record(op, saved_tokens, mode=mode, inp=input_tokens,
            out=output_tokens, detail=detail)
    return stats()


def prewarm() -> Dict[str, Any]:
    """预热模型 + 缓存收益报告 (供 PreCompact hook 调用)。

    收益化: 1) 模型已加载 → 压缩零等待
            2) 从记账库统计 cache_hit 累计节省 (缓存闭环的收益可见)
    """
    t0 = time.time()
    ok = _backend.chat("你是压缩引擎", "ok", max_tokens=3, timeout=90)
    el = round(time.time() - t0, 1)
    _record("prewarm", 0, "llm" if ok else "rule", 0, 0, "elapsed=%ss" % el)

    # 缓存收益: 累计 cache_hit 节省
    cache_benefit = {"hits": 0, "saved_tokens": 0}
    try:
        conn = _connect(DB_PATH)
        _init_db(conn)
        hits, saved = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(saved_tokens),0) FROM accounting "
            "WHERE op='cache_hit'").fetchone()
        cache_benefit = {"hits": hits or 0, "saved_tokens": saved or 0}
        conn.close()
    except Exception:
        pass

    return {"ok": ok is not None, "model": _backend.model,
            "model_ready": _backend.model_ready(), "elapsed_s": el,
            "cache_benefit": cache_benefit,
            "cache": cache_stats()}


def weekly_report(weeks: int = 8) -> Dict[str, Any]:
    """Token 节省周报:按周聚合 saved/ops/金额,附带操作分布。"""
    try:
        conn = _connect(DB_PATH)
        _init_db(conn)
        rows = conn.execute(
            "SELECT strftime('%Y-W%W', ts) AS wk,"
            " COUNT(*) AS ops,"
            " SUM(saved_tokens) AS saved,"
            " SUM(input_tokens) AS inp,"
            " SUM(output_tokens) AS out"
            " FROM accounting GROUP BY wk ORDER BY wk DESC LIMIT ?",
            (weeks,)).fetchall()
        by_op = conn.execute(
            "SELECT op, COUNT(*), COALESCE(SUM(saved_tokens),0)"
            " FROM accounting GROUP BY op ORDER BY 3 DESC").fetchall()
        conn.close()
        series = [{"week": w, "ops": o, "saved_tokens": s,
                   "input_tokens": i, "output_tokens": oo}
                  for w, o, s, i, oo in rows]
        total_saved = sum(s["saved_tokens"] for s in series)
        return {
            "weeks": series,
            "by_op": [{"op": o, "ops": c, "saved_tokens": s} for o, c, s in by_op],
            "total_saved_tokens": total_saved,
            "estimated_cost_saved_yuan": round(total_saved / 1e6 * COST_IN, 4),
            "cost_rate": "¥%s/M input, ¥%s/M output" % (COST_IN, COST_OUT),
        }
    except Exception as e:
        return {"error": str(e)}


def weekly_report_md(weeks: int = 8) -> str:
    """周报的 Markdown 表格形式(便于贴到聊天/文档)。"""
    r = weekly_report(weeks)
    if "error" in r:
        return "周报生成失败: %s" % r["error"]
    lines = ["## 📊 Token-Saver 周报", ""]
    lines.append("| 周 | 操作数 | 节省 token | 输入 token | 输出 token |")
    lines.append("|:--|--:|--:|--:|--:|")
    for w in r["weeks"]:
        lines.append("| %s | %d | %d | %d | %d |" % (
            w["week"], w["ops"], w["saved_tokens"],
            w["input_tokens"], w["output_tokens"]))
    if not r["weeks"]:
        lines.append("| (暂无数据) | 0 | 0 | 0 | 0 |")
    lines.append("")
    lines.append("**累计节省**: %s token ≈ ¥%.4f" % (
        r["total_saved_tokens"], r["estimated_cost_saved_yuan"]))
    if r["by_op"]:
        lines.append("")
        lines.append("**按操作**: " + " | ".join(
            "%s×%d(省%s)" % (o["op"], o["ops"], o["saved_tokens"]) for o in r["by_op"]))
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# CLI 入口(供 PreCompact hook 等脚本直接调用)
# ════════════════════════════════════════════════════════════════

def _cli() -> None:
    import sys
    args = sys.argv[1:]
    if not args:
        print("usage: python token_saver_mcp_server.py --cli {health|stats|summarize|trim|prewarm|weekly} [args]")
        return
    if args[0] == "--cli":
        args = args[1:]
    cmd = args[0] if args else "health"
    if cmd == "health":
        print(json.dumps(health(), ensure_ascii=False, indent=2))
    elif cmd == "stats":
        print(json.dumps(stats(), ensure_ascii=False, indent=2))
    elif cmd == "summarize" and len(args) >= 2:
        print(json.dumps(summarize(args[1]), ensure_ascii=False, indent=2))
    elif cmd == "trim" and len(args) >= 2:
        print(json.dumps(trim_tool_output(args[1]), ensure_ascii=False, indent=2))
    elif cmd == "prewarm":
        print(json.dumps(prewarm(), ensure_ascii=False, indent=2))
    elif cmd == "weekly":
        weeks = int(args[1]) if len(args) >= 2 and args[1].isdigit() else 8
        if len(args) >= 2 and args[1] in ("md", "markdown", "--md"):
            print(weekly_report_md(weeks))
        else:
            print(json.dumps(weekly_report(weeks), ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"error": "unknown cmd"}, ensure_ascii=False))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        _cli()
    else:
        mcp.run()
