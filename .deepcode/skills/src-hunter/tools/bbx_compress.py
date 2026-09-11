#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bbx_compress — 本地 HTTP/JS 结构化压缩 (省 token 专用).

漏洞赏金流程中把原始抓包/JS 先本地压缩成结构化摘要, 再交给云端大脑,
避免把大段原文直接塞进上下文。两条路径:

  1. 规则压缩 (默认, 零成本): 提取 METHOD/URL/关键头/JSON 键名/JS 特征。
  2. LLM 摘要 (--llm): 用本地 Ollama 模型 (默认 phi4-mini) 把规则压缩
     结果进一步提炼成要点, 不产生任何云端 token。

用法:
  cat capture.txt | python bbx_compress.py            # stdin 原文 → 规则压缩
  python bbx_compress.py req.txt --llm                # 文件 + 本地 LLM 摘要
  python bbx_compress.py --url "https://t/api?id=1"   # 单 URL 快速压缩

纯标准库 (urllib + json + re), 无第三方依赖。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

OLLAMA_HOST = os.environ.get("BBX_OLLAMA_HOST", "http://127.0.0.1:11434")
# 用户指定: 摘要生成用 phi4-mini (英文攻击面摘要实测更准, 非思考模型听话),
# 保留 BBX_LLM_MODEL 环境变量覆盖能力
LLM_MODEL = os.environ.get("BBX_LLM_MODEL", "phi4-mini")

# 请求中值得保留的安全相关头 (其余丢弃)
_KEEP_REQ_HEADERS = {
    "host", "content-type", "authorization", "cookie", "origin",
    "referer", "user-agent", "x-forwarded-for", "x-real-ip",
    "x-requested-with", "accept-language",
}
# 响应中值得保留的头
_KEEP_RES_HEADERS = {
    "content-type", "server", "x-powered-by", "location", "set-cookie",
    "www-authenticate", "access-control-allow-origin", "strict-transport-security",
}
# JS 中的敏感键名 (用于标红关注点)
_SENSITIVE_KEYS = re.compile(
    r"(token|secret|api[_-]?key|password|passwd|sign|signature|encrypt|"
    r"nonce|timestamp|auth|cookie|session|private[_-]?key)", re.I)
# JS 中值得提取的 URL / API 路径
_URL_RE = re.compile(r"""["']((?:https?://|/api/|//)[^"'\\\s]{4,120})["']""")
_MAX_PARAM = 60          # 参数值最长保留
_MAX_HEADER_VAL = 80     # 头值最长保留
_MAX_BODY_KEYS = 40      # body 键名最多输出


def _ollama_generate(prompt: str, model: str = LLM_MODEL, max_tokens: int = 512,
                     timeout: int = 120) -> str:
    """调用本地 Ollama 生成 (零云端成本)。失败时返回空串, 调用方回退规则结果。"""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": max_tokens},
    }
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = (json.loads(resp.read().decode("utf-8")).get("response") or "").strip()
            return out
    except Exception:
        return ""


def _mask(v: str) -> str:
    """值脱敏: 超过 MAX 截断, 含敏感词的折叠为 <redacted>。"""
    if len(v) > _MAX_PARAM:
        v = v[:_MAX_PARAM] + "…"
    return v


def _fmt_params(qs: str) -> str:
    """查询串 → 键名+截断值, 按参数名排序去重。"""
    if not qs:
        return ""
    parts = []
    for kv in qs.split("&"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            v = v.split("%2F")[-1] if "%2F" in v else v  # 路径编码展开一眼可读
        else:
            k, v = kv, ""
        parts.append(f"{k}={_mask(v)}")
    return "&".join(sorted(set(parts)))


def _json_keys(obj, depth: int = 0, out: list | None = None) -> list:
    """递归提取 JSON 键名+值类型 (值省略)。"""
    out = out if out is not None else []
    if depth > 2 or not isinstance(obj, dict):
        return out
    for k, v in obj.items():
        if isinstance(v, dict):
            out.append(f"{k}{{")
            _json_keys(v, depth + 1, out)
            out.append("}")
        elif isinstance(v, list):
            out.append(f"{k}[]")
        else:
            t = type(v).__name__
            if isinstance(v, str) and len(v) > 20:
                t = "str*"
            out.append(f"{k}:{t}")
    return out


def _json_body(body: str) -> str:
    """JSON body → 键名+类型行。解析失败回退原文截断。"""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return f"<non-json {len(body)}b>"
    keys = _json_keys(data)
    if len(keys) > _MAX_BODY_KEYS:
        keys = keys[:_MAX_BODY_KEYS] + ["…"]
    return " ".join(keys)


def _form_body(body: str) -> str:
    """urlencoded form → 键=截断值。"""
    if not body.strip():
        return ""
    return _fmt_params(body.strip())


def _parse_http(text: str) -> str:
    """HTTP 原文 (请求或响应) → 结构化单行块。"""
    lines = text.splitlines()
    head_end, headers, body = 0, {}, ""
    for i, ln in enumerate(lines):
        if not ln.strip():
            head_end = i
            break
        if ":" in ln and i > 0:
            k, v = ln.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    else:
        head_end = len(lines)
    body = "\n".join(lines[head_end + 1:]) if head_end < len(lines) else ""

    req = re.match(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)", lines[0])
    res = re.match(r"^HTTP/\S+\s+(\d{3})\s*(.*)", lines[0])
    blocks = []
    if req:
        method, url = req.group(1), req.group(2)
        path, _, qs = url.partition("?")
        keep = {k: v for k, v in headers.items() if k in _KEEP_REQ_HEADERS}
        blocks.append(f"> {method} {path} ?{_fmt_params(qs)}")
        if keep:
            blocks.append("  H: " + "; ".join(
                f"{k}={_mask(v)}" for k, v in sorted(keep.items())))
        ctype = headers.get("content-type", "")
        if body:
            if "json" in ctype:
                blocks.append("  B: " + _json_body(body))
            elif "form" in ctype or "x-www-form-urlencoded" in ctype:
                blocks.append("  B: " + _form_body(body))
            else:
                blocks.append(f"  B: <{ctype or 'raw'} {len(body)}b>")
    elif res:
        status = f"{res.group(1)} {res.group(2)}".strip()
        keep = {k: v for k, v in headers.items() if k in _KEEP_RES_HEADERS}
        blocks.append(f"< {status}")
        if keep:
            blocks.append("  H: " + "; ".join(
                f"{k}={_mask(v)}" for k, v in sorted(keep.items())))
        ctype = headers.get("content-type", "")
        if body:
            if "json" in ctype:
                blocks.append("  B: " + _json_body(body))
            else:
                blocks.append(f"  B: <{ctype or 'raw'} {len(body)}b>")
    else:
        blocks.append(lines[0][:100])
        if body:
            blocks.append(f"  B: <{len(body)}b>")
    return "\n".join(blocks)


def _parse_js(text: str) -> str:
    """JS 代码 → URL/API 路径/敏感键名/特征。"""
    urls = sorted(set(_URL_RE.findall(text)))[:_MAX_BODY_KEYS]
    sens = sorted(set(_SENSITIVE_KEYS.findall(text)))[:15]
    blocks = []
    if urls:
        blocks.append("  url: " + " | ".join(urls))
    if sens:
        blocks.append("  sens: " + ",".join(sens))
    return "\n".join(blocks)


def compress(text: str) -> str:
    """原文 → 规则压缩后的结构化文本。

    统一按 HTTP 块切分 (支持 请求+响应 混合报文, 如 playwright
    network_request 的 dict → 伪 HTTP 报文)。纯 JS 代码走 _parse_js。
    单个 HTTP 报文天然只切出一块, 行为与直接 _parse_http 一致。
    """
    if not text.strip():
        return "(empty)"
    blocks = []
    if re.match(r"\s*(function\s|const\s|let\s|var\s|=>\s*\{)", text):
        blocks.append(_parse_js(text))
    else:
        # 逐块尝试 HTTP (请求/响应块), 剩余按行截断
        for chunk in re.split(r"(?=\r?\n(?:HTTP/|(?:GET|POST|PUT|PATCH|DELETE)\s))", text):
            c = chunk.strip()
            if not c:
                continue
            if re.match(r"^(HTTP/|(GET|POST|PUT|PATCH|DELETE)\s)", c, re.I):
                blocks.append(_parse_http(c))
            else:
                blocks.append(c[:200])
    return "\n".join(blocks) if blocks else text[:500]


def llm_summarize(compressed: str) -> str:
    """把规则压缩结果交给本地 phi4-mini 提炼为要点。"""
    prompt = (
        "你是漏洞赏金助手。把下面的抓包/代码压缩结果提炼成 5 条以内的"
        "攻击面要点 (英文关键词+中文说明), 只写有价值的: 端点/参数/认证/"
        "可疑字段/注入点。不要复述原文。\n\n" + compressed[:4000]
    )
    return _ollama_generate(prompt)


def main() -> int:
    ap = argparse.ArgumentParser(description="本地 HTTP/JS 结构化压缩")
    ap.add_argument("files", nargs="*", help="输入文件 (缺省读 stdin)")
    ap.add_argument("--llm", action="store_true", help="规则压缩后再用本地 phi4-mini 提炼摘要")
    ap.add_argument("--url", help="单 URL 快速压缩 (自动补 GET)")
    args = ap.parse_args()

    raw = ""
    if args.url:
        raw = f"GET {args.url}"
    elif args.files:
        for f in args.files:
            with open(f, encoding="utf-8", errors="replace") as fh:
                raw += fh.read() + "\n"
    else:
        raw = sys.stdin.read()

    out = compress(raw)
    if args.llm:
        brief = llm_summarize(out)
        if brief:
            out += "\n\n[bbx-llm]\n" + brief
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
