#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ollama 公共客户端 — 统一封装 generate / embed
════════════════════════════════════════════
供 ollama_tools 下所有工具复用，风格对齐 cerebellum_core.py (urllib, 无第三方依赖)。

用法:
    from ollama_client import generate, embed, ensure_ollama
    text = generate("总结这段文字", system="你是复盘助手")
    vec  = embed(["第一条", "第二条"])
"""
from __future__ import annotations

import json
import sys
import urllib.request
from typing import Dict, List, Optional

# Windows 控制台 UTF-8 兼容 (emoji/中文输出，避免 GBK UnicodeEncodeError)
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

OLLAMA_HOST = "http://127.0.0.1:11434"
LLM_MODEL = "qwen2.5:3b"          # 通用文本 (摘要/分类/改写)
REASON_MODEL = "deepseek-r1:1.5b"  # 推理
EMBED_MODEL = "nomic-embed-text"   # 向量嵌入
YG_MODEL = "yaogu-analyst"         # 领域定制: 妖股/短线分析专家 (Modelfile 创建)


def model_available(name: str) -> bool:
    """检查指定模型是否存在"""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return any(m["name"] == name or m["name"].startswith(name)
                   for m in data.get("models", []))
    except Exception:
        return False


def _post(path: str, payload: Dict, timeout: int = 180) -> Dict:
    req = urllib.request.Request(
        f"{OLLAMA_HOST}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ollama_status() -> bool:
    """Ollama 是否在线"""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = [m["name"] for m in data.get("models", [])]
        return any(m.startswith(EMBED_MODEL) for m in models)
    except Exception:
        return False


def ensure_ollama() -> bool:
    """检查并提示 (exit code 供脚本直接退出)"""
    if ollama_status():
        return True
    print(f"[ollama_tools] ❌ Ollama 未就绪 ({OLLAMA_HOST})，请先启动: ollama serve", file=sys.stderr)
    return False


def generate(
    prompt: str,
    model: str = LLM_MODEL,
    system: str = "",
    temperature: float = 0.3,
    timeout: int = 180,
    raw: bool = False,
    format: Optional[dict] = None,
) -> str:
    """调用 Ollama generate，返回纯文本。失败抛出异常由调用方兜底。

    Args:
        format: Ollama JSON 模式 schema, 如 {"type": "object", "properties": {...}}
                设置后模型输出保证为合法 JSON
    """
    payload: Dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if system:
        payload["system"] = system
    if raw:
        payload["raw"] = True
    if format:
        payload["format"] = format
    resp = _post("/api/generate", payload, timeout=timeout)
    return (resp.get("response") or "").strip()


def embed(texts: List[str], timeout: int = 120) -> List[List[float]]:
    """批量向量化 (nomic-embed-text)"""
    if not texts:
        return []
    resp = _post("/api/embed", {"model": EMBED_MODEL, "input": texts}, timeout=timeout)
    return resp.get("embeddings", [])


def cosine(a: List[float], b: List[float]) -> float:
    """余弦相似度"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb + 1e-9)


def safe_generate(prompt: str, **kw) -> str:
    """带降级的生成：Ollama 不可用时返回空串，不抛异常。"""
    try:
        return generate(prompt, **kw)
    except Exception as e:
        print(f"[ollama_tools] ⚠️ 本地模型调用失败: {e}", file=sys.stderr)
        return ""


if __name__ == "__main__":
    print("ollama ok:", ollama_status())
    print("生成测试:", safe_generate("用一句话介绍你自己", temperature=0.7))
